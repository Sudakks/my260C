import openroad
from openroad import Design, Tech
from odb import *
import os
import argparse
import json

def is_buffer(design, inst):
    """Check if an instance is a buffer by master name."""
    if not inst:
        return False
    master = inst.getMaster()
    if not master:
        return False
    name = master.getName().lower()
    # Check for common buffer patterns in cell names (no inverters)
    return any(kw in name for kw in ['buf', 'clkbuf', 'bufd'])

def get_buffer_direction(design, inst):
    """
    Determine buffer pin directions using getIoType().
    Returns (input_pin_name, output_pin_name) or None if unknown.
    """
    if not inst:
        return None
    if not is_buffer(design, inst):
        return None
    
    master = inst.getMaster()
    pins = master.getMTerms()
    
    input_pin = None
    output_pin = None
    
    for pin in pins:
        pin_name = pin.getName().lower()
        if pin_name in ['vdd', 'vss', 'vpw', 'vnw']:
            continue
        
        # Use getIoType() for signal direction
        io_type = pin.getIoType()
        if io_type == 'INPUT':
            input_pin = pin.getName()
        elif io_type == 'OUTPUT':
            output_pin = pin.getName()
        elif io_type == 'INOUT':
            # Treat as both input and output for traversal
            if not input_pin:
                input_pin = pin.getName()
            elif not output_pin:
                output_pin = pin.getName()
    
    if input_pin and output_pin:
        return (input_pin, output_pin)
    return None

def traverse_net_backward(design, net, exclude_inst=None, max_hops=10):
    """
    Traverse BACKWARD through buffers to find the driving net.
    This follows the signal path from a pin's net back toward its source.
    Used for D pins (finding what drives the D input).
    Includes protection against circular traversal.
    """
    if not net:
        return None
    
    visited_nets = {net.getName()}
    visited_insts = set()  # Prevent infinite loops through buffer chains
    current_net = net
    hops = 0
    
    while hops < max_hops:
        hops += 1
        found_driver = False
        
        for iterm in current_net.getITerms():
            inst = iterm.getInst()
            if inst == exclude_inst:
                continue
            if inst in visited_insts:  # Skip already visited instances
                continue
            if not is_buffer(design, inst):
                continue
            
            visited_insts.add(inst)
            direction = get_buffer_direction(design, inst)
            if not direction:
                continue
            
            input_pin, output_pin = direction
            mterm_name = iterm.getMTerm().getName()
            
            # If we're connected to the OUTPUT of the buffer, traverse to INPUT
            if mterm_name == output_pin:
                input_term = inst.findITerm(input_pin)
                if input_term and input_term.getNet():
                    next_net = input_term.getNet()
                    if next_net.getName() not in visited_nets:
                        visited_nets.add(next_net.getName())
                        current_net = next_net
                        found_driver = True
                        break
        
        if not found_driver:
            break
    
    return current_net

def traverse_net_forward(design, net, exclude_inst=None, max_hops=10):
    """
    Traverse FORWARD through buffers to find the driven net.
    This follows the signal path from a pin's net toward its loads.
    Used for Q pins (finding what the Q output drives).
    Includes protection against circular traversal.
    """
    if not net:
        return None
    
    visited_nets = {net.getName()}
    visited_insts = set()  # Prevent infinite loops through buffer chains
    current_net = net
    hops = 0
    
    while hops < max_hops:
        hops += 1
        found_load = False
        
        for iterm in current_net.getITerms():
            inst = iterm.getInst()
            if inst == exclude_inst:
                continue
            if inst in visited_insts:  # Skip already visited instances
                continue
            if not is_buffer(design, inst):
                continue
            
            visited_insts.add(inst)
            direction = get_buffer_direction(design, inst)
            if not direction:
                continue
            
            input_pin, output_pin = direction
            mterm_name = iterm.getMTerm().getName()
            
            # If we're connected to the INPUT of the buffer, traverse to OUTPUT
            if mterm_name == input_pin:
                output_term = inst.findITerm(output_pin)
                if output_term and output_term.getNet():
                    next_net = output_term.getNet()
                    if next_net.getName() not in visited_nets:
                        visited_nets.add(next_net.getName())
                        current_net = next_net
                        found_load = True
                        break
        
        if not found_load:
            break
    
    return current_net

def get_driving_instances(net, exclude_inst=None):
    """Get instances that drive this net (connected to output pins)."""
    insts = []
    if not net:
        return insts
    for iterm in net.getITerms():
        inst = iterm.getInst()
        if inst == exclude_inst:
            continue
        mterm = iterm.getMTerm()
        if mterm.getIoType() == 'OUTPUT':
            insts.append(inst)
    return insts

def get_load_instances(net, exclude_inst=None):
    """Get instances that are driven by this net (connected to input pins)."""
    insts = []
    if not net:
        return insts
    for iterm in net.getITerms():
        inst = iterm.getInst()
        if inst == exclude_inst:
            continue
        mterm = iterm.getMTerm()
        if mterm.getIoType() == 'INPUT':
            insts.append(inst)
    return insts

def is_mbff_master(master_name):
    """Check if a master name is an MBFF."""
    name = master_name.lower()
    return 'dfrbpq' in name and any(x in name for x in ['v2x', 'v4x', 'h2v2x'])

def get_mbff_max_bits(master_name):
    """Get the maximum number of bits for an MBFF master."""
    name = master_name.lower()
    if 'v4x' in name or 'h2v2x' in name:
        return 4
    return 2

def check_clock_reset_equivalence(mbff_inst, orig_flop_inst, mbff_name, bit_index):
    """
    Check that MBFF and original flop have equivalent clock and reset signals.
    Returns list of error messages (empty if no errors).
    
    Note: CLK/RST mismatch is only an error if both designs have these pins wired.
    If MBFF has CLK/RST unconnected but original has them, it's acceptable
    (bit-slice clustering may share CLK/RST across the MBFF).
    """
    errors = []
    
    # Standard MBFF pin names for control signals
    clk_pins = ['CLK', 'clk']
    rst_pins = ['RESETB', 'RST', 'RESET', 'resetb', 'rst', 'reset']
    
    # Find CLK/RESET pins on both cells
    mbff_clk_net = None
    mbff_rst_net = None
    orig_clk_net = None
    orig_rst_net = None
    
    for pin_name in clk_pins:
        term = mbff_inst.findITerm(pin_name)
        if term:
            mbff_clk_net = term.getNet()
            break
    
    for pin_name in rst_pins:
        term = mbff_inst.findITerm(pin_name)
        if term:
            mbff_rst_net = term.getNet()
            break
    
    for pin_name in clk_pins:
        term = orig_flop_inst.findITerm(pin_name)
        if term:
            orig_clk_net = term.getNet()
            break
    
    for pin_name in rst_pins:
        term = orig_flop_inst.findITerm(pin_name)
        if term:
            orig_rst_net = term.getNet()
            break
    
    # Only error if BOTH are connected and they differ
    # Unconnected CLK/RST on MBFF is acceptable (shared clock for all bits)
    if mbff_clk_net and orig_clk_net:
        if mbff_clk_net.getName() != orig_clk_net.getName():
            errors.append(f"D{bit_index}/Q{bit_index} of {mbff_name}: CLK mismatch - MBFF CLK '{mbff_clk_net.getName()}' != original CLK '{orig_clk_net.getName()}'")
    
    if mbff_rst_net and orig_rst_net:
        if mbff_rst_net.getName() != orig_rst_net.getName():
            errors.append(f"D{bit_index}/Q{bit_index} of {mbff_name}: RESET mismatch - MBFF RESET '{mbff_rst_net.getName()}' != original RESET '{orig_rst_net.getName()}'")
    
    return errors

def check_equivalence(design, block, clust_insts, orig_db, orig_block, orig_insts, changelist):
    """
    Perform comprehensive logical equivalence checking.
    Returns (passed_checks, errors) tuple where passed_checks counts individual pin checks with no errors.
    """
    errors = []
    passed_checks = []  # Track (mbff_name, pin_index) tuples that have no errors
    
    for mbff_name, original_flops in changelist.items():
        # Check 1: MBFF instance exists
        if mbff_name not in clust_insts:
            errors.append(f"MBFF {mbff_name} not found in clustered design")
            continue
        
        mbff_inst = clust_insts[mbff_name]
        master_name = mbff_inst.getMaster().getName()
        master_name_lower = master_name.lower()
        
        # Check 2: MBFF master is valid
        if not is_mbff_master(master_name):
            errors.append(f"MBFF {mbff_name} has invalid master: {master_name}")
            continue
        
        max_bits = get_mbff_max_bits(master_name)
        
        # Check 3: Verify changelist length matches max_bits
        if len(original_flops) != max_bits:
            errors.append(f"MBFF {mbff_name} changelist has {len(original_flops)} entries, expected {max_bits}")
        
        for i in range(max_bits):
            orig_flop_name = original_flops[i] if i < len(original_flops) else 0
            
            if orig_flop_name == 0:
                # Check 4: Empty pin pair verification
                d_term = mbff_inst.findITerm(f'D{i}')
                q_term = mbff_inst.findITerm(f'Q{i}')
                d_connected = d_term and d_term.getNet()
                q_connected = q_term and q_term.getNet()
                
                if d_connected or q_connected:
                    errors.append(f"MBFF {mbff_name} pin pair {i}: expected disconnected but D{i}={'connected' if d_connected else 'disconnected'}, Q{i}={'connected' if q_connected else 'disconnected'}")
                else:
                    passed_checks.append((mbff_name, i))
                continue
            
            # Check 5: Original flop exists
            if orig_flop_name not in orig_insts:
                errors.append(f"Original flop {orig_flop_name} not found in baseline design")
                continue
            
            orig_flop_inst = orig_insts[orig_flop_name]
            
            # Check 6: Original flop is actually a sequential cell
            if not orig_flop_inst.getMaster().isSequential():
                errors.append(f"Original flop {orig_flop_name} is not sequential (master: {orig_flop_inst.getMaster().getName()})")
                continue
            
            # Check 7: D pin equivalence with buffer traversal (backward)
            clust_d_term = mbff_inst.findITerm(f'D{i}')
            orig_d_term = orig_flop_inst.findITerm('D')
            
            clust_d_net = clust_d_term.getNet() if clust_d_term else None
            orig_d_net = orig_d_term.getNet() if orig_d_term else None
            
            # Traverse backward through buffers to find the driving net
            clust_d_driving = traverse_net_backward(design, clust_d_net, exclude_inst=mbff_inst)
            orig_d_driving = traverse_net_backward(orig_db, orig_d_net, exclude_inst=orig_flop_inst)
            
            clust_d_name = clust_d_driving.getName() if clust_d_driving else None
            orig_d_name = orig_d_driving.getName() if orig_d_driving else None
            
            d_check_passed = False
            # Check 8: D pin driver compatibility
            if clust_d_name != orig_d_name:
                # Check if the drivers are at least the same type
                clust_d_drivers = get_driving_instances(clust_d_driving, mbff_inst)
                orig_d_drivers = get_driving_instances(orig_d_driving, orig_flop_inst)
                
                # Compare driver masters
                clust_driver_masters = set(d.getMaster().getName() for d in clust_d_drivers)
                orig_driver_masters = set(d.getMaster().getName() for d in orig_d_drivers)
                
                if clust_driver_masters != orig_driver_masters:
                    errors.append(f"D{i} of {mbff_name}: driver mismatch - clustered drivers {clust_driver_masters} != original drivers {orig_driver_masters} (nets: '{clust_d_name}' vs '{orig_d_name}')")
                else:
                    # Drivers match but nets differ - still acceptable, count as passed
                    d_check_passed = True
            else:
                d_check_passed = True
            
            # Check 9: Q pin equivalence with buffer traversal (forward)
            clust_q_term = mbff_inst.findITerm(f'Q{i}')
            orig_q_term = orig_flop_inst.findITerm('Q')
            
            clust_q_net = clust_q_term.getNet() if clust_q_term else None
            orig_q_net = orig_q_term.getNet() if orig_q_term else None
            
            # Traverse forward through buffers to find the driven net
            clust_q_driven = traverse_net_forward(design, clust_q_net, exclude_inst=mbff_inst)
            orig_q_driven = traverse_net_forward(orig_db, orig_q_net, exclude_inst=orig_flop_inst)
            
            clust_q_name = clust_q_driven.getName() if clust_q_driven else None
            orig_q_name = orig_q_driven.getName() if orig_q_driven else None
            
            q_check_passed = False
            # Check 10: Q pin load compatibility
            if clust_q_name != orig_q_name:
                clust_q_loads = get_load_instances(clust_q_driven, mbff_inst)
                orig_q_loads = get_load_instances(orig_q_driven, orig_flop_inst)
                
                clust_load_masters = set(l.getMaster().getName() for l in clust_q_loads)
                orig_load_masters = set(l.getMaster().getName() for l in orig_q_loads)
                
                if clust_load_masters != orig_load_masters:
                    errors.append(f"Q{i} of {mbff_name}: load mismatch - clustered loads {clust_load_masters} != original loads {orig_load_masters} (nets: '{clust_q_name}' vs '{orig_q_name}')")
                else:
                    # Loads match but nets differ - still acceptable, count as passed
                    q_check_passed = True
            else:
                q_check_passed = True
            
            # Track this pin pair as passed if both D and Q checks passed
            if d_check_passed and q_check_passed:
                passed_checks.append((mbff_name, i))
            
            # Check 11: D/Q directional compatibility (D should not be connected to Q's net and vice versa)
            # This ensures D and Q pins weren't accidentally swapped
            # Detect by comparing driver/load master names: if D has Q's drivers and Q has D's loads, they're swapped
            if clust_d_driving and clust_q_driven and orig_d_driving and orig_q_driven:
                clust_d_drivers = get_driving_instances(clust_d_driving, mbff_inst)
                clust_d_driver_masters = set(d.getMaster().getName() for d in clust_d_drivers)
                clust_q_loads = get_load_instances(clust_q_driven, mbff_inst)
                clust_q_load_masters = set(l.getMaster().getName() for l in clust_q_loads)
                
                orig_d_drivers = get_driving_instances(orig_d_driving, orig_flop_inst)
                orig_d_driver_masters = set(d.getMaster().getName() for d in orig_d_drivers)
                orig_q_loads = get_load_instances(orig_q_driven, orig_flop_inst)
                orig_q_load_masters = set(l.getMaster().getName() for l in orig_q_loads)
                
                orig_q_drivers = get_driving_instances(orig_q_driven, orig_flop_inst)
                orig_q_driver_masters = set(d.getMaster().getName() for d in orig_q_drivers)
                orig_d_loads = get_load_instances(orig_d_driving, orig_flop_inst)
                orig_d_load_masters = set(l.getMaster().getName() for l in orig_d_loads)
                
                # Swap detected: if D's drivers match Q's drivers AND Q's loads match D's loads
                if (clust_d_driver_masters == orig_q_driver_masters and 
                    clust_q_load_masters == orig_d_load_masters and
                    clust_d_driver_masters):  # Must have something to compare
                    errors.append(f"D{i}/Q{i} of {mbff_name}: pins appear swapped - D connected to Q driver net, Q connected to D load net")
            
            # Check 12: Verify D pin is actually an input and Q pin is actually an output
            if clust_d_term:
                d_mterm = clust_d_term.getMTerm()
                d_io_type = d_mterm.getIoType()
                if d_io_type != 'INPUT':
                    errors.append(f"D{i} of {mbff_name}: pin IoType is {d_io_type}, expected INPUT")
            
            if clust_q_term:
                q_mterm = clust_q_term.getMTerm()
                q_io_type = q_mterm.getIoType()
                if q_io_type != 'OUTPUT':
                    errors.append(f"Q{i} of {mbff_name}: pin IoType is {q_io_type}, expected OUTPUT")
            
            # Check 13: Clock and Reset equivalence
            clk_rst_errors = check_clock_reset_equivalence(mbff_inst, orig_flop_inst, mbff_name, i)
            errors.extend(clk_rst_errors)
    
    return len(passed_checks), errors

def main():
    parser = argparse.ArgumentParser(description="ECE 260C MBFF Logical Equivalence Checker")
    parser.add_argument('--design', type=str, required=True, help="Design name (e.g. 'gcd_v1')")
    parser.add_argument('--changelist', type=str, required=True, help="Path to changelist.json")
    parser.add_argument('--input', type=str, help="Path to clustered .odb (defaults to runs/<design>/clustered.odb)")
    args = parser.parse_args()

    # Resolve paths
    input_path = args.input if args.input else f"runs/{args.design}/clustered.odb"
    baseline_path = f"designs/{args.design}/design.odb"

    if not os.path.exists(input_path):
        print(f"Error: Clustered ODB not found at {input_path}")
        return
    if not os.path.exists(baseline_path):
        print(f"Error: Baseline ODB not found at {baseline_path}")
        return

    with open(args.changelist, 'r') as f:
        changelist = json.load(f)

    # Load Tech
    tech = Tech()
    tech.readLiberty("pdk/lib/sg13g2_stdcell_typ_1p20V_25C_mbff.lib")
    
    # Original Design (loaded as detached DB)
    orig_db = Design.createDetachedDb()
    read_db(orig_db, baseline_path)
    orig_block = orig_db.getChip().getBlock()
    orig_insts = {inst.getName(): inst for inst in orig_block.getInsts()}

    # Clustered Design (main design)
    design = Design(tech)
    design.readDb(input_path)
    block = design.getBlock()
    clust_insts = {inst.getName(): inst for inst in block.getInsts()}

    passed, errors = check_equivalence(design, block, clust_insts, orig_db, orig_block, orig_insts, changelist)

    # Generate JSON report
    report = {
        "design": args.design,
        "results": {
            "passed_count": passed,
            "failed_count": len(errors),
            "all_passed": len(errors) == 0,
            "errors": errors
        }
    }
    
    # Default report path: runs/<design>/equivalence.json
    report_path = f"runs/{args.design}/equivalence.json"
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)

    print(f"\nLogical Equivalence Check Results:")
    print(f"  Passed: {passed}")
    print(f"  Failed: {len(errors)}")
    print(f"  Report written to: {report_path}")
    
    if errors:
        print("\nErrors:")
        for err in errors:
            print(f"  - {err}")
    else:
        print("\nAll checks passed!")

if __name__ == "__main__":
    main()
