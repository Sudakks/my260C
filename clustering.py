import openroad, odb
from openroad import Design, Tech, Timing
from odb import *
import os
import argparse
from glob import glob
# --- Import additional packages here ---
import json
from collections import defaultdict
import math


# -------------- Helper Functions ------------------

def find_mbff_master(db, master_name):
    """
    修复1: 每个MBFF master住在自己独立的同名library里，
    必须遍历所有libs才能找到，不能只查 getLibs()[0]。
    """
    for lib in db.getLibs():
        m = lib.findMaster(master_name)
        if m is not None:
            return m
    return None


def get_flip_flops(block):
    flops = []
    for inst in block.getInsts():
        master = inst.getMaster()
        if not master.isSequential():
            continue
        name_lower = master.getName().lower()
        if any(x in name_lower for x in ['v2x', 'v4x', 'h2v2x']):
            continue  # 跳过已有的MBFF
        if 'dfrbp' not in name_lower:
            continue  # 只处理dfrbp系列
        flops.append(inst)
    return flops


def get_clk_rst_nets(inst):
    """
    返回 (clk_net_name, rst_net_name)。
    
    关键：CLK经过clock buffer后每个FF连的local net名各不相同，
    所以不能直接用net名分组——必须追溯到driver端的根net。
    RST同理。这里用 trace_to_root 追溯到第一个非buffer driver的net。
    """
    clk_net = rst_net = None
    for iterm in inst.getITerms():
        pin = iterm.getMTerm().getName().upper()
        net = iterm.getNet()
        if net is None:
            continue
        if pin == 'CLK':
            clk_net = trace_to_root(net)
        elif pin in ('RESET_B', 'RESETB', 'RST', 'RESET'):
            rst_net = trace_to_root(net)
    return clk_net, rst_net


def trace_to_root(net, max_hops=10):
    """
    向上追溯net：如果net的driver是一个buffer/inverter，
    就跳到buffer的输入net，直到找到真正的信号源net。
    返回根net的名字（字符串）。
    
    这样，哪怕CLK经过多级buffer，所有FF都能归到同一个root CLK net。
    """
    if net is None:
        return None
    current = net
    visited = set()
    for _ in range(max_hops):
        name = current.getName()
        if name in visited:
            break
        visited.add(name)
        # 找这个net上的driver（OUTPUT pin的inst）
        driver_iterm = None
        for iterm in current.getITerms():
            if iterm.getMTerm().getIoType() == 'OUTPUT':
                driver_iterm = iterm
                break
        if driver_iterm is None:
            break  # 没有driver（可能是primary input），停止追溯
        driver_inst = driver_iterm.getInst()
        driver_master = driver_inst.getMaster().getName().lower()
        # 只穿过 buffer（不穿过inverter、FF、组合逻辑）
        if 'buf' not in driver_master and 'clkbuf' not in driver_master:
            break
        # 找buffer的输入net
        input_net = None
        for iterm in driver_inst.getITerms():
            if iterm.getMTerm().getIoType() == 'INPUT':
                input_net = iterm.getNet()
                break
        if input_net is None:
            break
        current = input_net
    return current.getName()


def get_center(inst):
    loc = inst.getLocation()
    m = inst.getMaster()
    return loc[0] + m.getWidth() // 2, loc[1] + m.getHeight() // 2


def cluster_by_proximity(flops, max_dist_dbu, prefer_4bit=True):
    remaining = list(flops)
    clusters = []
    target = 4 if prefer_4bit else 2
    while remaining:
        seed = remaining.pop(0)
        sx, sy = get_center(seed)
        cluster = [seed]
        dists = sorted(
            remaining,
            key=lambda f: abs(get_center(f)[0] - sx) + abs(get_center(f)[1] - sy)
        )
        for cand in dists:
            if len(cluster) >= target:
                break
            cx, cy = get_center(cand)
            if abs(cx - sx) + abs(cy - sy) <= max_dist_dbu:
                cluster.append(cand)
                remaining.remove(cand)
        if len(cluster) >= 2:
            # 凑不到4个时，拆成2+剩余
            if target == 4 and len(cluster) == 3:
                clusters.append(cluster[:2])
                remaining.insert(0, cluster[2])
            else:
                clusters.append(cluster)
    return clusters


def get_pin_net(inst, pin_names):
    for name in pin_names:
        iterm = inst.findITerm(name)
        if iterm and iterm.getNet():
            return iterm.getNet()
    return None


def replace_cluster_with_mbff(block, db, cluster, changelist):
    n = len(cluster)
    if n == 2:
        master_name = "sg13g2_dfrbpq_V2X_1"
    else:
        master_name = "sg13g2_dfrbpq_V4X_1"

    mbff_master = find_mbff_master(db, master_name)
    if mbff_master is None:
        print(f"  FAIL: cannot find master '{master_name}'")
        return None

    xs = [get_center(f)[0] for f in cluster]
    ys = [get_center(f)[1] for f in cluster]
    cx, cy = int(sum(xs) / n), int(sum(ys) / n)

    ff_data = []
    for ff in cluster:
        ff_data.append({
            'name': ff.getName(),
            'inst': ff,
            'd_net':   get_pin_net(ff, ['D']),
            'q_net':   get_pin_net(ff, ['Q']),
            'qn_net':  get_pin_net(ff, ['Q_N', 'QN']),
            'clk_net': get_pin_net(ff, ['CLK']),
            'rst_net': get_pin_net(ff, ['RESET_B', 'RESETB', 'RST', 'RESET']),
        })

    # 打印第一个cluster的详细信息用于调试
    if len(changelist) == 0:  # 只打印第一次
        print(f"  DEBUG first cluster:")
        for d in ff_data:
            print(f"    FF={d['name']} D={d['d_net'].getName() if d['d_net'] else None} "
                  f"Q={d['q_net'].getName() if d['q_net'] else None} "
                  f"CLK={d['clk_net'].getName() if d['clk_net'] else None} "
                  f"RST={d['rst_net'].getName() if d['rst_net'] else None}")

    def get_rst_driver_name(rst_net_obj):
        if rst_net_obj is None:
            return None
        current = rst_net_obj
        visited = set()
        for _ in range(20):
            name = current.getName()
            if name in visited:
                break
            visited.add(name)
            driver_iterm = None
            for iterm in current.getITerms():
                if iterm.getMTerm().getIoType() == 'OUTPUT':
                    driver_iterm = iterm
                    break
            if driver_iterm is None:
                return current.getName()
            driver_inst = driver_iterm.getInst()
            driver_master = driver_inst.getMaster().getName().lower()
            if 'buf' in driver_master or 'inv' in driver_master or 'clkbuf' in driver_master:
                input_net = None
                for iterm in driver_inst.getITerms():
                    if iterm.getMTerm().getIoType() == 'INPUT':
                        input_net = iterm.getNet()
                        break
                if input_net:
                    current = input_net
                    continue
            return current.getName()
        return current.getName()

    rst_drivers = set(get_rst_driver_name(d['rst_net']) for d in ff_data)
    print(f"  DEBUG rst_drivers = {rst_drivers}")  # ← 关键：看RST追溯结果
    
    if len(rst_drivers) > 1:
        from collections import defaultdict as _dd
        rst_subgroups = _dd(list)
        for d in ff_data:
            key = get_rst_driver_name(d['rst_net']) or '__none__'
            rst_subgroups[key].append(d)
        ff_data = max(rst_subgroups.values(), key=len)
        print(f"  DEBUG after RST split: {len(ff_data)} FFs remain")
        if len(ff_data) < 2:
            print(f"  FAIL: after RST split only {len(ff_data)} FF left")
            return None
        n = len(ff_data)
        if n > 4: ff_data = ff_data[:4]; n = 4
        elif n == 3: ff_data = ff_data[:2]; n = 2
        master_name = "sg13g2_dfrbpq_V2X_1" if n == 2 else "sg13g2_dfrbpq_V4X_1"
        mbff_master = find_mbff_master(db, master_name)
        if mbff_master is None:
            print(f"  FAIL: cannot find master '{master_name}' after RST split")
            return None
        xs = [get_center(d['inst'])[0] for d in ff_data]
        ys = [get_center(d['inst'])[1] for d in ff_data]
        cx, cy = int(sum(xs) / n), int(sum(ys) / n)

    # 以下保持不变（创建MBFF、连线、删除原FF）
    mbff_name = block.makeNewInstName("mbff")
    mbff_inst = dbInst.create(block, mbff_master, mbff_name)
    mbff_inst.setLocation(cx, cy)
    mbff_inst.setPlacementStatus("PLACED")

    clk_net = ff_data[0]['clk_net']
    if clk_net:
        t = mbff_inst.findITerm('CLK')
        if t: t.connect(clk_net)

    rst_net = ff_data[0]['rst_net']
    if rst_net:
        t = mbff_inst.findITerm('RESET_B')
        if t: t.connect(rst_net)

    for i, data in enumerate(ff_data):
        if data['d_net']:
            t = mbff_inst.findITerm(f'D{i}')
            if t: t.connect(data['d_net'])
        if data['q_net']:
            t = mbff_inst.findITerm(f'Q{i}')
            if t: t.connect(data['q_net'])
        if data['qn_net']:
            t = mbff_inst.findITerm(f'Q_N{i}')
            if t: t.connect(data['qn_net'])

    for data in ff_data:
        for iterm in data['inst'].getITerms():
            iterm.disconnect()
        dbInst.destroy(data['inst'])

    changelist[mbff_name] = [data['name'] for data in ff_data]
    return mbff_name


# --- Do not edit except to add additional, optional parameters ---

parser = argparse.ArgumentParser(description="ECE 260C MBFF Clustering")

parser.add_argument(
    '--design',
    type=str,
    help="Your design to load. e.g., 'gcd_v1'",
    required=True
)
parser.add_argument(
    '--output',
    type=str,
    help="Output path (defaults to runs/<design>/clustered.odb)"
)


args = parser.parse_args()
tech = Tech()


print("Loading design...")
design = Design(tech)
tech.readLiberty("pdk/lib/sg13g2_stdcell_typ_1p20V_25C_mbff.lib")

design.readDb(f"designs/{args.design}/design.odb")
# Our design databases already have the MBFF LEF files loaded into them.
library = design.getDb().getLibs()[0]

design.evalTclString(f"source pdk/setRC.tcl")
design.evalTclString(f"read_sdc designs/{args.design}/constraints.sdc")
library = design.getDb().getLibs()[0]
dbu_per_micron = library.getDbUnitsPerMicron()
block = design.getBlock()
# 拿到 db 对象，用于跨lib查找MBFF master
db = design.getDb()

print("Performing MBFF clustering...")
# --- Your Code Below ---

MAX_DIST_UM = 15.0
max_dist_dbu = int(MAX_DIST_UM * dbu_per_micron)

print("Finding flip-flops...")
all_flops = get_flip_flops(block)
print(f"  {len(all_flops)} single-bit FFs found")

print("Grouping by CLK (root net)...")
# 只按CLK root net分组。
# RST不用于分组——GCD的reset经过inverter，trace_to_root无法穿过，
# 导致每个FF的rst net名各不同，把所有FF分散成35个单独的组。
# RST的一致性在replace时检查（同一cluster里所有FF的RST net必须相同）。
groups = defaultdict(list)
for ff in all_flops:
    clk, rst = get_clk_rst_nets(ff)
    if clk is not None:
        groups[clk].append(ff)

print(f"  {len(groups)} CLK groups")
for clk_key, grp in sorted(groups.items(), key=lambda x: -len(x[1]))[:5]:
    print(f"    clk='{clk_key}' -> {len(grp)} FFs")

print("Clustering...")
all_clusters = []
for clk_key, group in groups.items():
    if len(group) < 2:
        continue
    clusters = cluster_by_proximity(group, max_dist_dbu, prefer_4bit=True)
    all_clusters.extend([c for c in clusters if len(c) >= 2])

print(f"  {len(all_clusters)} clusters formed")

print("Replacing with MBFFs...")
changelist = {}
ok = fail = 0
for cluster in all_clusters:
    if len(cluster) not in (2, 4):
        continue
    try:
        result = replace_cluster_with_mbff(block, db, cluster, changelist)
        if result:
            ok += 1
        else:
            fail += 1
    except Exception as e:
        import traceback
        print(f"  EXCEPTION in cluster of size {len(cluster)}: {e}")
        traceback.print_exc()
        fail += 1
        break  # 第一个错误就停，避免刷屏

print(f"  Created {ok} MBFFs, {fail} failed")

print("Legalizing...")
design.evalTclString("detailed_placement")

# 写出 changelist.json
output_path = args.output if args.output else f"runs/{args.design}"
os.makedirs(output_path, exist_ok=True)
with open(f"{output_path}/changelist.json", 'w') as f:
    json.dump(changelist, f, indent=2)
print(f"  changelist.json written")

print("Done.")

# --- Do not edit ---
print("Writing Database...")

design.writeDb(f"{output_path}/clustered.odb")
print(f"Wrote to {output_path}/clustered.odb")