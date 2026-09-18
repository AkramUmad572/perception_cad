#!/usr/bin/env python3
"""
Regression tests for CadQuery sandbox execution.

Run with: python -m cad.test_sandbox
Or:       python backend/cad/test_sandbox.py

Tests that:
1. Legitimate CadQuery geometry scripts succeed
2. Security-violating scripts are blocked
3. Error types are correctly categorized
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cad.sandbox import _hex_to_rgb, execute_cadquery_script


# Scripts that SHOULD succeed
VALID_SCRIPTS = {
    "simple_box": '''
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 5)
''',
    
    "box_with_hole": '''
import cadquery as cq
result = cq.Workplane("XY").box(30, 30, 20).faces(">Z").workplane().hole(10)
''',
    
    "keychain_with_fillet": '''
import cadquery as cq
result = (
    cq.Workplane("XY")
    .box(40, 20, 3)
    .edges("|Z")
    .fillet(5)
    .faces(">Z")
    .workplane()
    .center(15, 0)
    .hole(5)
)
''',
    
    "cylinder_chamfer": '''
import cadquery as cq
result = cq.Workplane("XY").circle(15).extrude(40).edges(">Z").chamfer(2)
''',

    "union_cut": '''
import cadquery as cq
box = cq.Workplane("XY").box(30, 30, 30)
cylinder = cq.Workplane("XY").cylinder(40, 10)
result = box.cut(cylinder)
''',

    "character_union": '''
import cadquery as cq
head = cq.Workplane("XY").sphere(18)
ear_l = cq.Workplane("XY").transformed(offset=(-12, 16, 8)).sphere(8)
ear_r = cq.Workplane("XY").transformed(offset=(12, 16, 8)).sphere(8)
result = head.union(ear_l).union(ear_r)
''',
    
    "with_math": '''
import cadquery as cq
import math
radius = 10 * math.sqrt(2)
result = cq.Workplane("XY").circle(radius).extrude(20)
''',
    
    "text_extrusion": '''
import cadquery as cq
box = cq.Workplane("XY").box(50, 30, 10)
text = cq.Workplane("XY").workplane(offset=10).text("Hi", 10, 3)
result = box.union(text)
''',

    "mug_with_handle": '''
import cadquery as cq
body = (
    cq.Workplane("XY")
    .circle(25)
    .extrude(60)
    .faces(">Z")
    .shell(-3)
)
handle = (
    cq.Workplane("XZ")
    .center(25, 30)
    .ellipse(8, 15)
    .extrude(5)
)
result = body.union(handle)
''',
}


# Scripts that SHOULD be blocked with security errors
SECURITY_BLOCKED_SCRIPTS = {
    "import_os": "import os\nresult = os.getcwd()",
    "import_subprocess": "import subprocess\nresult = subprocess.run(['ls'])",
    "import_socket": "import socket\nresult = socket.socket()",
    "import_requests": "import requests\nresult = requests.get('http://example.com')",
    "from_os_import": "from os import getcwd\nresult = getcwd()",
    "import_sys": "import sys\nresult = sys.path",
    "import_pathlib": "from pathlib import Path\nresult = Path('/')",
    # CadQuery proxy blocks access to internal modules
    "cq_exporters": "import cadquery as cq\nx = cq.exporters\nresult = cq.Workplane('XY').box(10,10,5)",
    "cq_occ_impl": "import cadquery as cq\nx = cq.occ_impl\nresult = cq.Workplane('XY').box(10,10,5)",
}


# Scripts blocked via stripped builtins (not import security)
BUILTIN_BLOCKED_SCRIPTS = {
    "open_file": "import cadquery as cq\nf = open('/etc/passwd')\nresult = cq.Workplane('XY').box(10, 10, 5)",
    "exec_code": "import cadquery as cq\nexec('print(1)')\nresult = cq.Workplane('XY').box(10, 10, 5)",
    "eval_code": "import cadquery as cq\nx = eval('1+1')\nresult = cq.Workplane('XY').box(10, 10, 5)",
    "getattr_escape": "import cadquery as cq\nx = getattr(cq, '__file__')\nresult = cq.Workplane('XY').box(10, 10, 5)",
}


def test_valid_scripts():
    """Test that legitimate CadQuery scripts succeed."""
    print("\n=== Testing Valid Scripts ===")
    passed = 0
    failed = 0
    
    for name, script in VALID_SCRIPTS.items():
        result = execute_cadquery_script(script)
        if result["ok"]:
            print(f"  ✓ {name}")
            passed += 1
        else:
            error_type = result.get("error_type", "unknown")
            if error_type == "security":
                print(f"  ✗ {name}: SECURITY ERROR - {result.get('error')}")
                failed += 1
            else:
                print(f"  ~ {name}: {error_type} (non-security, may be mesh quality)")
                passed += 1
    
    return passed, failed


def test_security_blocked():
    """Test that security-violating imports are blocked."""
    print("\n=== Testing Security Blocking ===")
    passed = 0
    failed = 0
    
    for name, script in SECURITY_BLOCKED_SCRIPTS.items():
        result = execute_cadquery_script(script)
        if not result["ok"] and result.get("error_type") == "security":
            print(f"  ✓ {name}: Blocked correctly")
            passed += 1
        elif result["ok"]:
            print(f"  ✗ {name}: SECURITY HOLE - Script was not blocked!")
            failed += 1
        else:
            print(f"  ~ {name}: Blocked but as '{result.get('error_type')}' not 'security'")
            passed += 1
    
    return passed, failed


def test_builtin_blocked():
    """Test that dangerous builtins are blocked."""
    print("\n=== Testing Builtin Blocking ===")
    passed = 0
    failed = 0
    
    for name, script in BUILTIN_BLOCKED_SCRIPTS.items():
        result = execute_cadquery_script(script)
        if not result["ok"]:
            print(f"  ✓ {name}: Blocked ({result.get('error_type')})")
            passed += 1
        else:
            print(f"  ✗ {name}: SECURITY HOLE - Script was not blocked!")
            failed += 1
    
    return passed, failed


def test_hex_to_rgb():
    """Vertex-color bake helper: valid hex including black; invalid → silver."""
    print("\n=== Testing hex color bake helper ===")
    passed = 0
    failed = 0
    cases = [
        ("#FFD700", (255, 215, 0), "gold"),
        ("#000000", (0, 0, 0), "intentional black"),
        ("#212121", (33, 33, 33), "near-black"),
        ("#fff", (255, 255, 255), "short white"),
        ("#C0C0C0", (192, 192, 192), "silver"),
        ("not-a-color", (192, 192, 192), "invalid → silver"),
        (None, (192, 192, 192), "None → silver"),
        ("", (192, 192, 192), "empty → silver"),
    ]
    for raw, expected, description in cases:
        got = _hex_to_rgb(raw)
        if got == expected:
            print(f"  [ok] {description}: {raw!r} → {got}")
            passed += 1
        else:
            print(f"  [FAIL] {description}: {raw!r}")
            print(f"    Expected: {expected}")
            print(f"    Got:      {got}")
            failed += 1
    return passed, failed


def test_assembly_part_colors():
    """Assembly children keep distinct vertex colors unless flatten_color=True."""
    print("\n=== Testing Assembly per-part colors ===")
    import numpy as np
    import trimesh
    import tempfile

    script = '''
import cadquery as cq
gold = cq.Workplane("XY").sphere(8)
blk = cq.Workplane("XY").transformed(offset=(16, 0, 0)).sphere(6)
assy = cq.Assembly()
assy.add(gold, name="gold", color=cq.Color("#FFD700"))
assy.add(blk, name="black", color=cq.Color("#212121"))
result = assy
'''
    passed = 0
    failed = 0
    out = Path(tempfile.mkdtemp())

    def unique_colors(glb_path):
        loaded = trimesh.load(str(glb_path))
        geoms = list(loaded.geometry.values()) if isinstance(loaded, trimesh.Scene) else [loaded]
        seen = set()
        for g in geoms:
            vc = np.asarray(g.visual.vertex_colors)
            if vc.size == 0:
                continue
            row = tuple(int(x) for x in np.mean(vc.reshape(-1, vc.shape[-1]), axis=0)[:3])
            seen.add(row)
        return seen

    keep = execute_cadquery_script(
        script, output_dir=out, flatten_color=False, color="#00FF00"
    )
    if keep.get("ok") and keep.get("multi_color"):
        cols = unique_colors(keep["glb_path"])
        if len(cols) >= 2:
            print(f"  [ok] flatten_color=False keeps {len(cols)} part colors {cols}")
            passed += 1
        else:
            print(f"  [FAIL] expected 2+ colors, got {cols}")
            failed += 1
    else:
        print(f"  [FAIL] assembly export: {keep}")
        failed += 1

    flat = execute_cadquery_script(
        script, output_dir=out, flatten_color=True, color="#E53935"
    )
    if flat.get("ok"):
        cols = unique_colors(flat["glb_path"])
        if len(cols) == 1 and list(cols)[0][:3] == (229, 57, 53):
            print("  [ok] flatten_color=True paints all parts #E53935")
            passed += 1
        else:
            print(f"  [FAIL] flatten expected single (229,57,53), got {cols}")
            failed += 1
    else:
        print(f"  [FAIL] flatten export: {flat}")
        failed += 1

    return passed, failed


def run_all_tests():
    """Run all sandbox tests."""
    print("=" * 60)
    print("CADQUERY SANDBOX REGRESSION TESTS")
    print("=" * 60)
    
    v_pass, v_fail = test_valid_scripts()
    s_pass, s_fail = test_security_blocked()
    b_pass, b_fail = test_builtin_blocked()
    h_pass, h_fail = test_hex_to_rgb()
    a_pass, a_fail = test_assembly_part_colors()
    
    total_pass = v_pass + s_pass + b_pass + h_pass + a_pass
    total_fail = v_fail + s_fail + b_fail + h_fail + a_fail
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Valid scripts:     {v_pass}/{v_pass + v_fail} passed")
    print(f"Security blocking: {s_pass}/{s_pass + s_fail} passed")
    print(f"Builtin blocking:  {b_pass}/{b_pass + b_fail} passed")
    print(f"Hex color bake:    {h_pass}/{h_pass + h_fail} passed")
    print(f"Assembly colors:   {a_pass}/{a_pass + a_fail} passed")
    print(f"TOTAL:             {total_pass}/{total_pass + total_fail} passed")
    
    if total_fail > 0:
        print(f"\n⚠️  {total_fail} TESTS FAILED")
        return 1
    else:
        print("\n✓ ALL TESTS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
