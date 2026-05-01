import openroad
from openroad import Design, Tech, Timing
from odb import *
import os
import argparse
import json
import re
import tempfile
import multiprocessing

parser = argparse.ArgumentParser(description="ECE 260C MBFF Evaluator")
parser.add_argument('--design', type=str, default=None, help="Design name (e.g. 'gcd_v1')")
parser.add_argument('--input', type=str, default=None, help="Input .odb path")
parser.add_argument('--sdc', type=str, default=None, help="SDC constraint file path")
parser.add_argument('--output', type=str, default=None, help="Output report.json path")
parser.add_argument('--baseline', action='store_true', help="Evaluate baseline from designs/")
parser.add_argument(
    '--threads',
    type=int,
    default=multiprocessing.cpu_count(),
    help="Thread Count"
)
args = parser.parse_args()


openroad.set_thread_count(args.threads)


# Resolve paths
if args.input and not args.design:
    design_name = os.path.splitext(os.path.basename(args.input))[0]
elif args.design:
    design_name = args.design
else:
    parser.error("either --design or --input is required")

if args.input:
    input_path = args.input
elif args.baseline:
    input_path = f"designs/{design_name}/design.odb"
else:
    input_path = f"runs/{design_name}/clustered.odb"

sdc_path = args.sdc or (f"designs/{design_name}/constraints.sdc" if os.path.exists(f"designs/{design_name}/constraints.sdc") else None)
output_path = args.output or f"runs/{design_name}/{'baseline.json' if args.baseline else 'report.json'}"

# --- Load Design ---
print(f"Loading design from {input_path}...")
tech = Tech()
tech.readLiberty("pdk/lib/sg13g2_stdcell_typ_1p20V_25C_mbff.lib")
design = Design(tech)
design.readDb(input_path)

# --- Helper: run Tcl command with echo ---
def tcl(cmd):
    print(f"$ {cmd}")
    return design.evalTclString(cmd)

tcl("source pdk/setRC.tcl")
if sdc_path:
    tcl(f"read_sdc {sdc_path}")

block = design.getBlock()
dbu = block.getDbUnitsPerMicron()
library = design.getDb().getLibs()[0]

baseline_path = f"designs/{design_name}/design.odb"
if os.path.exists(baseline_path):
    original_db = Design.createDetachedDb()
    read_db(original_db, baseline_path)

# --- Helper: run Tcl command with echo ---
def tcl(cmd):
    print(f"$ {cmd}")
    return design.evalTclString(cmd)

tcl("source pdk/setRC.tcl")
if sdc_path:
    tcl(f"read_sdc {sdc_path}")

# --- Pre-eval stats (calculated BEFORE flow modifies the design) ---
all_insts = list(block.getInsts())

# Count flip-flops: any sequential cell (excludes latches in this PDK)
# MBFFs are identified by V2X, V4X, or H2V2X in their name
def is_mbff(inst):
    """Check if instance is a multi-bit flip-flop."""
    name = inst.getMaster().getName().lower()
    return 'dfrbpq' in name and any(x in name for x in ['v2x', 'v4x', 'h2v2x'])

def is_ff(inst):
    """Check if instance is any flip-flop (single-bit or MBFF)."""
    master = inst.getMaster()
    if not master.isSequential():
        return False
    name = master.getName().lower()
    # Include dfrbpq and dfrbp variants (DFF with/without Q_N)
    return 'dfrbp' in name

total_ffs = sum(1 for i in all_insts if is_ff(i))
mbff_count = sum(1 for i in all_insts if is_mbff(i))
single_ff_count = total_ffs - mbff_count

mbff_types = {}
empty_pins = 0
for inst in all_insts:
    if is_mbff(inst):
        name = inst.getMaster().getName()
        mbff_types[name] = mbff_types.get(name, 0) + 1
        max_bits = 4 if 'v4x' in name.lower() or 'h2v2x' in name.lower() else 2
        for bit in range(max_bits):
            d_term = inst.findITerm(f'D{bit}')
            if d_term and d_term.getNet() is None:
                empty_pins += 1

# --- Flow: CTS -> DP -> pin_access -> global_route -> repair_design -> incremental GRT -> repair_timing -> incremental GRT ---
tcl("clock_tree_synthesis -sink_clustering_enable -repair_clock_nets")
tcl("estimate_parasitics -placement")
tcl("detailed_placement")
#tcl("pin_access")
tcl("global_route")
tcl("estimate_parasitics -global_routing")
tcl("repair_design")
tcl("global_route -start_incremental")
tcl("detailed_placement")
tcl("global_route -end_incremental")
tcl("estimate_parasitics -global_routing")
tcl("repair_timing -setup_margin 0 -hold_margin 0 -repair_tns 100 -verbose -skip_gate_cloning")
tcl("global_route -start_incremental")
tcl("detailed_placement")
tcl("global_route -end_incremental")
tcl("global_route -start_incremental")
tcl("global_route -end_incremental")
tcl("estimate_parasitics -global_routing")

# --- Post-eval stats ---
timing = Timing(design)
corners = timing.getCorners()
corner = corners[0] if corners else None

# --- Legalizability: displacement from original design ---
displacement = {"total_um": 0.0, "max_um": 0.0, "mean_um": 0.0}
if original_db:
    orig_block = original_db.getChip().getBlock()
    orig_insts = {inst.getName(): inst for inst in orig_block.getInsts()}
    total_disp = 0.0
    max_disp = 0.0
    count = 0
    for inst in block.getInsts():
        name = inst.getName()
        if name in orig_insts:
            orig_inst = orig_insts[name]
            orig_loc = orig_inst.getLocation()
            new_loc = inst.getLocation()
            dx = abs(new_loc[0] - orig_loc[0]) / dbu
            dy = abs(new_loc[1] - orig_loc[1]) / dbu
            manhattan = dx + dy
            total_disp += manhattan
            max_disp = max(max_disp, manhattan)
            count += 1
    displacement["total_um"] = round(total_disp, 2)
    displacement["max_um"] = round(max_disp, 2)
    displacement["mean_um"] = round(total_disp / count, 2) if count > 0 else 0

# --- Logical equivalence: compare against original design ---
original_combinational = set()
original_flops = {}  # name -> {D_net, Q_net}
if original_db:
    orig_block = original_db.getChip().getBlock()
    for inst in orig_block.getInsts():
        master = inst.getMaster()
        master_name = master.getName().lower()
        is_buf_inv = 'buf' in master_name or 'inv' in master_name
        if not master.isSequential() and not is_buf_inv:
            original_combinational.add(inst.getName())
        elif master.isSequential():
            d_term = inst.findITerm('D')
            q_term = inst.findITerm('Q')
            original_flops[inst.getName()] = {
                'D_net': d_term.getNet().getName() if d_term and d_term.getNet() else None,
                'Q_net': q_term.getNet().getName() if q_term and q_term.getNet() else None,
            }

post_insts = {inst.getName(): inst for inst in block.getInsts()}

def is_clock_related(inst):
    """Check if instance is connected to clock net or is a buffer/inverter."""
    master = inst.getMaster()
    if design.isBuffer(master) or design.isInverter(master):
        return True
    for iterm in inst.getITerms():
        net = iterm.getNet()
        if net and design.isInClock(iterm):
            return True
    return False

post_combinational = {n for n, i in post_insts.items()
                      if not i.getMaster().isSequential()
                      and not is_clock_related(i)}

# Only report missing original cells (repair cells are expected to be added)
missing_combinational = sorted(original_combinational - post_combinational)
added_combinational = sorted(post_combinational - original_combinational)

# Debug: print masters of missing combinational cells
if missing_combinational:
    print(f"\nMissing combinational cells ({len(missing_combinational)}):")
    for name in missing_combinational:
        orig_inst = orig_insts.get(name)
        if orig_inst:
            print(f"  {name}: {orig_inst.getMaster().getName()}")

# Debug: print missing combinational cells
if missing_combinational:
    print(f"\nMissing combinational cells ({len(missing_combinational)}):")
    for name in missing_combinational[:20]:
        print(f"  {name}")
    if len(missing_combinational) > 20:
        print(f"  ... and {len(missing_combinational) - 20} more")

# Check flopped connections preserved
flop_conn_issues = 0
for name, orig_nets in original_flops.items():
    post_inst = post_insts.get(name)
    if post_inst:
        d_term = post_inst.findITerm('D')
        q_term = post_inst.findITerm('Q')
        d_net = d_term.getNet().getName() if d_term and d_term.getNet() else None
        q_net = q_term.getNet().getName() if q_term and q_term.getNet() else None
        if d_net != orig_nets['D_net'] or q_net != orig_nets['Q_net']:
            flop_conn_issues += 1

# --- Clock period and TNS from STA ---
clock_period = None
tns = None
min_period_path = os.path.join(tempfile.gettempdir(), f"min_period_{design_name}.txt")
tcl(f"report_clock_min_period > {min_period_path}")
if os.path.exists(min_period_path):
    for line in open(min_period_path):
        m = re.search(r'period_min\s*=\s*([\d.]+)', line)
        if m:
            clock_period = float(m.group(1))
            break
    os.remove(min_period_path)

# Get TNS from report_tns
tns_path = os.path.join(tempfile.gettempdir(), f"tns_{design_name}.txt")
tcl(f"report_tns > {tns_path}")
if os.path.exists(tns_path):
    for line in open(tns_path):
        m = re.search(r'tns\s+max\s+([\-\d.]+)', line)
        if m:
            tns = float(m.group(1))
            break
    os.remove(tns_path)

# Wirelength from global router
grt = design.getGlobalRouter()
wl_file = os.path.join(tempfile.gettempdir(), f"wl_{design_name}.txt")
total_wirelength = 0.0
for net in block.getNets():
    if net.getSigType() == "SIGNAL" and not net.isSpecial():
        grt.reportNetWireLength(net, True, False, False, wl_file)
        if os.path.exists(wl_file):
            with open(wl_file) as f:
                content = f.read().strip()
                # Format: "grt: netname 79.2 2"
                parts = content.split()
                if len(parts) >= 3:
                    total_wirelength += float(parts[-2])
            os.remove(wl_file)
total_wirelength /= dbu

# CTS stats from report_cts
cts_path = os.path.join(tempfile.gettempdir(), f"cts_{design_name}.txt")
tcl(f"report_cts -out_file {cts_path}")
cts_stats = {"buffers": 0, "sinks": 0}
if os.path.exists(cts_path):
    for line in open(cts_path):
        m = re.search(r'Total number of Buffers Inserted:\s*(\d+)', line)
        if m: cts_stats["buffers"] = int(m.group(1))
        m = re.search(r'Total number of Sinks:\s*(\d+)', line)
        if m: cts_stats["sinks"] = int(m.group(1))
    os.remove(cts_path)

# Power
total_static = total_dynamic = 0.0
if corner:
    for inst in all_insts:
        total_static += timing.staticPower(inst, corner)
        total_dynamic += timing.dynamicPower(inst, corner)

# Instance count and area
instance_count = len(all_insts)
total_area = sum(inst.getMaster().getArea() for inst in all_insts) / (dbu * dbu)

# --- Report ---
report = {
    "design": design_name,
    "instance_count": instance_count,
    "total_area": round(total_area, 2),
    "total_ffs": total_ffs,
    "mbff_count": mbff_count,
    "single_ff_count": total_ffs - mbff_count,
    "mbff_ratio": mbff_count / total_ffs if total_ffs else 0,
    "mbff_maseters": mbff_types,
    "empty_pins": empty_pins,
    "clock_buffer_count": cts_stats["buffers"],
    "sink_count": cts_stats["sinks"],
    "total_wirelength": round(total_wirelength, 2),
    "static_power": round(total_static, 6),
    "total_power": round(total_dynamic, 6),
    "displacement": displacement,
    "missing_comb_cells": missing_combinational,
    "added_comb_cells": added_combinational,
    "half_connected_pin_pairs": flop_conn_issues,
    "expected_clock_period": clock_period,
    "total_negative_slack": tns,
}

os.makedirs(os.path.dirname(output_path), exist_ok=True)
with open(output_path, 'w') as f:
    json.dump(report, f, indent=2)

print(f"Report written to {output_path}")
print(json.dumps(report, indent=2))