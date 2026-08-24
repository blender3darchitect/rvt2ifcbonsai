"""
fix_cad2data.py — Aggressive repair of cad2data IFC for Bonsai

The crash at collector.py:125 happens because elements are contained
in IfcSpaces that have no parent in the spatial hierarchy (no
IfcRelAggregates connecting them to an IfcBuildingStorey).

This script takes the nuclear approach: every element currently
contained in an IfcSpace gets moved to a storey directly, bypassing
the broken IfcSpace chain entirely. IfcSpaces without a parent get
aggregated into a fallback storey.

Also fixes IfcShapeAspect.ProductDefinitional for Optimise compat.

Usage:
  python fix_cad2data.py hotel.ifc
  python fix_cad2data.py hotel.ifc hotel_fixed.ifc
"""

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import sys
import os


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
        for space in orphan_spaces:
            # Find best storey by comparing elevations if possible
            best_storey = fallback
            try:
                space_z = space.ObjectPlacement.RelativePlacement.Location.Coordinates[2]
                for storey in storeys:
                    if storey.Elevation is not None and storey.Elevation <= space_z:
                        best_storey = storey
            except Exception:
                pass

            try:
                ifcopenshell.api.run(
                    "aggregate.assign_object",
                    f,
                    products=[space],
                    relating_object=best_storey,
                )
                fixes += 1
            except Exception as e:
                print(f"    Could not aggregate IfcSpace #{space.id()} '{space.Name}': {e}")
        print(f"  Aggregated {len(orphan_spaces)} IfcSpaces into storeys")
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
            target = fallback
            # Try to find best storey by element elevation
            try:
                coords = element.ObjectPlacement.RelativePlacement.Location.Coordinates
                z = coords[2] if len(coords) > 2 else 0
                for storey in storeys:
                    if storey.Elevation is not None and storey.Elevation <= z:
                        target = storey
            except Exception:
                pass

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
        print("Usage: python fix_cad2data_v2.py input.ifc [output.ifc]")
        sys.exit(1)

    inp = sys.argv[1]
    if len(sys.argv) >= 3:
        out = sys.argv[2]
    else:
        base, ext = os.path.splitext(inp)
        out = f"{base}_fixed{ext}"

    fix_file(inp, out)
