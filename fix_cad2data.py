"""
fix_cad2data.py — Repair of cad2data IFC for Bonsai

Version 0.0.4

The crash at collector.py:125 happens because elements are contained
in IfcSpaces that have no parent in the spatial hierarchy (no
IfcRelAggregates connecting them to an IfcBuildingStorey).

IfcSpaces without a parent are aggregated into the storey matching
their absolute elevation. Elements whose containment chain still fails
to resolve to a storey are reassigned directly to one, bypassing the
broken IfcSpace.

Also fixes IfcShapeAspect.ProductDefinitional for Optimise compat.

Usage:
  python fix_cad2data.py hotel.ifc
  python fix_cad2data.py hotel.ifc hotel_fixed.ifc
"""

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import ifcopenshell.util.placement
import sys
import os

__version__ = "0.0.4"

# STATUS: early days. This has been validated against a small number of
# cad2data conversions only. It needs testing against a much wider range of
# Revit files — multi-building projects, linked models, mezzanines and split
# levels, and IFC2x3 output as well as IFC4 — and will likely need additional
# fixes as those cases turn up defects this script does not yet handle.
# Treat a successful run as "worth checking in Bonsai", not as a guarantee.


def find_storey_by_elevation(element, storeys, fallback):
    """Pick the storey an element belongs to by comparing elevations.

    Returns the highest IfcBuildingStorey whose Elevation is at or below the
    element's absolute Z, or `fallback` if the position cannot be determined.

    The Z must be absolute. IfcLocalPlacement.RelativePlacement.Location is
    relative to the parent placement, and cad2data leaves that at the origin
    for IfcSpaces — every space reads as Z=0 and matches only a below-grade
    storey. get_local_placement() resolves the full placement chain, which is
    what makes the comparison against storey Elevation meaningful.
    """
    try:
        z = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)[2][3]
    except Exception:
        return fallback

    best, best_elevation = fallback, None
    for storey in storeys:
        elevation = storey.Elevation
        if elevation is None or elevation > z + 1e-6:
            continue
        if best_elevation is None or elevation > best_elevation:
            best, best_elevation = storey, elevation
    return best


def find_parent_storey(element, f):
    """Walk up the spatial hierarchy to find the parent IfcBuildingStorey.
    Uses both aggregation (IfcRelAggregates) and containment paths."""
    visited = set()
    current = element

    while current and current.id() not in visited:
        visited.add(current.id())

        if current.is_a("IfcBuildingStorey"):
            return current

        # Try aggregation (IfcRelDecomposes / IfcRelAggregates)
        parent = ifcopenshell.util.element.get_aggregate(current)
        if parent:
            current = parent
            continue

        # Try containment
        container = ifcopenshell.util.element.get_container(current)
        if container and container.id() not in visited:
            current = container
            continue

        break

    return None


def fix_file(input_path, output_path):
    print(f"fix_cad2data {__version__}")
    print(f"Opening {input_path}...")
    f = ifcopenshell.open(input_path)
    print(f"Schema: {f.schema}")

    # --- Diagnostics ---
    storeys = f.by_type("IfcBuildingStorey")
    spaces = f.by_type("IfcSpace")
    elements = f.by_type("IfcElement")

    print(f"\nSpatial structure:")
    print(f"  IfcProject:        {len(f.by_type('IfcProject'))}")
    print(f"  IfcSite:           {len(f.by_type('IfcSite'))}")
    print(f"  IfcBuilding:       {len(f.by_type('IfcBuilding'))}")
    print(f"  IfcBuildingStorey: {len(storeys)}")
    print(f"  IfcSpace:          {len(spaces)}")
    print(f"  IfcElement:        {len(elements)}")

    if not storeys:
        print("\nFATAL: No IfcBuildingStorey found. Cannot repair.")
        sys.exit(1)

    # Pick fallback storey (lowest elevation)
    try:
        fallback = sorted(storeys, key=lambda s: s.Elevation or 0)[0]
    except Exception:
        fallback = storeys[0]
    print(f"\nFallback storey: '{fallback.Name}' (#{fallback.id()})")

    fixes = 0

    # === FIX 1: IfcSpaces without parent in aggregation hierarchy ===
    print("\n=== Fix 1: IfcSpace aggregation ===")
    orphan_spaces = []
    for space in spaces:
        parent = ifcopenshell.util.element.get_aggregate(space)
        if parent is None:
            orphan_spaces.append(space)

    if orphan_spaces:
        print(f"  Found {len(orphan_spaces)} IfcSpace(s) not aggregated into any storey")
        assigned = {}
        for space in orphan_spaces:
            best_storey = find_storey_by_elevation(space, storeys, fallback)

            try:
                ifcopenshell.api.run(
                    "aggregate.assign_object",
                    f,
                    products=[space],
                    relating_object=best_storey,
                )
                assigned[best_storey.Name] = assigned.get(best_storey.Name, 0) + 1
                fixes += 1
            except Exception as e:
                print(f"    Could not aggregate IfcSpace #{space.id()} '{space.Name}': {e}")
        print(f"  Aggregated {sum(assigned.values())} IfcSpaces into storeys:")
        for name, count in sorted(assigned.items(), key=lambda kv: -kv[1]):
            print(f"    {count:5d} -> '{name}'")
        if len(assigned) == 1 and len(storeys) > 1:
            print("  WARNING: every IfcSpace landed on a single storey.")
            print("  This usually means the elevation lookup failed — check the result in Bonsai.")
    else:
        print("  All IfcSpaces have parent aggregation — OK")

    # === FIX 2: Elements in IfcSpaces whose chain is broken ===
    print("\n=== Fix 2: Elements with broken containment chain ===")
    broken_elements = []
    for element in elements:
        container = ifcopenshell.util.element.get_container(element)
        if container is None:
            broken_elements.append((element, None))
        elif container.is_a("IfcSpace"):
            # Check if this IfcSpace resolves to a storey
            storey = find_parent_storey(container, f)
            if storey is None:
                broken_elements.append((element, container))

    if broken_elements:
        print(f"  Found {len(broken_elements)} elements with broken chain")
        no_container = sum(1 for _, c in broken_elements if c is None)
        in_orphan_space = sum(1 for _, c in broken_elements if c is not None)
        print(f"    No container at all: {no_container}")
        print(f"    In IfcSpace with no parent storey: {in_orphan_space}")

        for element, space in broken_elements:
            target = find_storey_by_elevation(element, storeys, fallback)

            try:
                ifcopenshell.api.run(
                    "spatial.assign_container",
                    f,
                    products=[element],
                    relating_structure=target,
                )
                fixes += 1
            except Exception:
                pass

        print(f"  Reassigned {len(broken_elements)} elements to storeys")
    else:
        print("  All elements have valid containment chains — OK")

    # === FIX 3: Remove null IfcRelContainedInSpatialStructure ===
    print("\n=== Fix 3: Null containment relationships ===")
    removed = 0
    for rel in f.by_type("IfcRelContainedInSpatialStructure"):
        try:
            if rel.RelatingStructure is None:
                f.remove(rel)
                removed += 1
                fixes += 1
        except Exception:
            f.remove(rel)
            removed += 1
            fixes += 1
    print(f"  Removed {removed} null containment relationships")

    # === FIX 4: Null IfcRelAggregates ===
    print("\n=== Fix 4: Null aggregation relationships ===")
    removed_agg = 0
    for rel in f.by_type("IfcRelAggregates"):
        try:
            if rel.RelatingObject is None:
                f.remove(rel)
                removed_agg += 1
                fixes += 1
        except Exception:
            f.remove(rel)
            removed_agg += 1
            fixes += 1
    print(f"  Removed {removed_agg} null aggregation relationships")

    # === FIX 5: IfcShapeAspect ===
    print("\n=== Fix 5: IfcShapeAspect.ProductDefinitional ===")
    sa_fixed = 0
    for sa in f.by_type("IfcShapeAspect"):
        try:
            if sa.ProductDefinitional is None:
                sa.ProductDefinitional = False
                sa_fixed += 1
                fixes += 1
        except Exception:
            sa.ProductDefinitional = False
            sa_fixed += 1
            fixes += 1
    print(f"  Fixed {sa_fixed} entities")

    # === FIX 6: Converter branding in the STEP header ===
    # cad2data stamps its vendor name into the FILE_NAME organization and
    # authorization fields, which Bonsai surfaces as Organisation and
    # Authoriser in the Project Info panel. These describe who authored and
    # signed off the data, not which tool wrote it — the converter already
    # identifies itself in preprocessor_version and IfcApplication, both of
    # which are left alone.
    print("\n=== Fix 6: File header attribution ===")
    header_fixed = 0
    try:
        file_name = f.header.file_name

        organization = list(file_name.organization or [])
        if any((entry or "").strip() for entry in organization):
            print(f"  Organisation: {organization} -> blank")
            file_name.organization = [""]
            header_fixed += 1
            fixes += 1

        if (file_name.authorization or "").strip():
            print(f"  Authoriser:   '{file_name.authorization}' -> blank")
            file_name.authorization = ""
            header_fixed += 1
            fixes += 1

        if not header_fixed:
            print("  Organisation and Authoriser already blank — OK")
    except Exception as e:
        print(f"  Could not read or edit the file header: {e}")

    # === DIAGNOSTIC: Verify no broken chains remain ===
    print("\n=== Verification pass ===")
    still_broken = 0
    for element in f.by_type("IfcElement"):
        container = ifcopenshell.util.element.get_container(element)
        if container is None:
            still_broken += 1
        elif container.is_a("IfcSpace"):
            storey = find_parent_storey(container, f)
            if storey is None:
                still_broken += 1

    if still_broken:
        print(f"  WARNING: {still_broken} elements still have broken chains")
        print(f"  These may cause Bonsai to crash on import")
    else:
        print(f"  All containment chains verified — should be safe for Bonsai")

    # === DIAGNOSTIC: Void report ===
    print("\n=== Void summary ===")
    void_counts = {}
    for rel in f.by_type("IfcRelVoidsElement"):
        el = rel.RelatingBuildingElement
        if el:
            void_counts[el.id()] = void_counts.get(el.id(), 0) + 1
    if void_counts:
        total = sum(void_counts.values())
        max_v = max(void_counts.values())
        print(f"  Total voids: {total}, max per element: {max_v}")
        high = sum(1 for v in void_counts.values() if v > 10)
        print(f"  Elements with >10 voids: {high}")

    # === Save ===
    print(f"\n{'='*50}")
    print(f"Total fixes applied: {fixes}")

    if fixes > 0:
        print(f"Saving to {output_path}...")
        f.write(output_path)
        in_mb = os.path.getsize(input_path) / (1024 * 1024)
        out_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"Input:  {in_mb:.1f} MB")
        print(f"Output: {out_mb:.1f} MB")
    else:
        print("No fixes needed.")

    print(f"\nTry opening {output_path} in Bonsai.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fix_cad2data.py input.ifc [output.ifc]")
        sys.exit(1)

    inp = sys.argv[1]
    if len(sys.argv) >= 3:
        out = sys.argv[2]
    else:
        base, ext = os.path.splitext(inp)
        out = f"{base}_fixed{ext}"

    fix_file(inp, out)
