import openroad, odb
from openroad import Design, Tech, Timing
from odb import *
import os
import argparse
from glob import glob
# --- Import additional packages here ---




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

print("Performing MBFF clustering...")
# --- Your Code Below --- 





# --- Do not edit ---
print("Writing Database...")

output_path = args.output if args.output else f"runs/{args.design}"

os.makedirs(output_path, exist_ok=True)

design.writeDb(f"{output_path}/clustered.odb")
print(f"Wrote to {output_path}/clustered.odb")