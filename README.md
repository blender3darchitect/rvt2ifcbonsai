# fix_cad2data — Repair cad2data (ODA) IFC Files for Bonsai BIM

***English** · [Português (Brasil)](README.pt-BR.md)*

A Python script that fixes structural defects in IFC files produced by the [cad2data](https://github.com/datadrivenconstruction/cad2data-Revit-IFC-DWG-DGN) Revit-to-IFC converter, making them compatible with [Bonsai BIM](https://bonsaibim.org) (the Blender BIM add-on).

This enables a fully free, offline, no-Revit pipeline: `.rvt` → cad2data → `fix_cad2data.py` → Bonsai.

---

## The Problem

### Symptoms

When opening a cad2data-produced IFC file in Bonsai, three things happen:

1. **Bonsai crashes during import** with this traceback:

```
File "bonsai/tool/collector.py", line 125, in assign
    while container.is_a("IfcSpace"):
          ^^^^^^^^^^^^^^
AttributeError: 'NoneType' object has no attribute 'is_a'
```

2. **Walls render with missing geometry** — Bonsai logs hundreds of warnings:

```
Warning! Excessive voids were found and skipped for the following elements:
#80801=IfcWall(...) - 22 openings
#40324=IfcWall(...) - 24 openings
```

3. **The file size is roughly 2.4× larger** than the same model exported from Revit's native IFC exporter (e.g., 60 MB vs 25 MB).

The same IFC file opens correctly in BIMvision, FreeCAD (NativeIFC mode), and other viewers that don't require a complete spatial hierarchy to display geometry.

### What viewers "see" vs what Bonsai needs

Most IFC viewers treat the file as a flat list of geometric objects. They iterate through elements, render their geometry, and display them. Whether an element has a valid spatial container is irrelevant to rendering.

Bonsai is different. It organizes every IFC element into Blender's collection hierarchy, which mirrors the IFC spatial structure:

```
IfcProject
  └── IfcSite
        └── IfcBuilding
              └── IfcBuildingStorey (Ground Floor)
                    ├── IfcWall
                    ├── IfcDoor
                    └── IfcSpace (Room 101)
                          └── IfcFurnishingElement (Table)
```

To place an element in the right collection, Bonsai must walk up the spatial hierarchy until it finds an `IfcBuildingStorey`. If an element is contained in an `IfcSpace`, Bonsai walks from the `IfcSpace` upward to its parent storey. If that chain is broken — if the `IfcSpace` has no parent — the walk returns `None`, and Bonsai crashes.

---

## Root Cause: Broken IfcRelAggregates

### How spatial hierarchy works in IFC

IFC uses two different relationship types for spatial organization:

| Relationship | Connects | Purpose |
|---|---|---|
| `IfcRelContainedInSpatialStructure` | Elements → Spatial containers | "This wall is on the ground floor" |
| `IfcRelAggregates` | Spatial containers → Parent containers | "This room is part of the ground floor" |

The critical distinction:

- **Elements** (walls, doors, slabs) are **contained** in spatial containers via `IfcRelContainedInSpatialStructure`
- **Spatial containers** (IfcSpace, IfcBuildingStorey) are **aggregated** into their parents via `IfcRelAggregates`

These are two separate relationship types with different entity classes in the IFC schema. A tool that creates one but not the other produces a file that looks correct on the surface but has a broken chain.

### What cad2data (ODA) does wrong

The cad2data converter, which wraps the Open Design Alliance (ODA) BimRv SDK, correctly creates:

- `IfcRelContainedInSpatialStructure` relationships placing elements inside `IfcSpace` entities
- The `IfcSpace` entities themselves, with names and geometry

But it **does not create** the `IfcRelAggregates` relationships that connect those `IfcSpace` entities to their parent `IfcBuildingStorey`. The IfcSpaces exist as orphans — they have children (the elements inside them) but no parent in the spatial tree.

```
What Revit's native exporter produces:       What cad2data produces:

IfcBuildingStorey (Ground Floor)             IfcBuildingStorey (Ground Floor)
  │                                            │
  ├── IfcRelAggregates ──► IfcSpace            │  (no IfcRelAggregates)
  │                          │                 │
  │                          ├── IfcWall       IfcSpace ◄── ORPHAN
  │                          └── IfcDoor         │
  │                                              ├── IfcWall
  ├── IfcWall (directly contained)               └── IfcDoor
  └── IfcSlab
                                               IfcWall (directly contained)
                                               IfcSlab
```

### Why Bonsai crashes

Bonsai's `collector.py` module assigns each element to a Blender collection. The relevant code path:

```python
# collector.py, line ~125
# For each element, find its spatial container
container = get_container(element)

# If the container is an IfcSpace, walk up to find the storey
while container.is_a("IfcSpace"):
    container = get_aggregate(container)  # ← returns None for orphan IfcSpaces
    # Next iteration: None.is_a("IfcSpace") → AttributeError
```

The loop assumes every `IfcSpace` has a parent via `IfcRelAggregates`. When it doesn't, `get_aggregate()` returns `None`, and the next iteration crashes.

### Why the file is larger

This is a separate issue from the crash. Revit's native IFC exporter uses:

- **Parametric geometry** (`IfcExtrudedAreaSolid`) — a wall is a profile + height, a few hundred bytes
- **Geometry reuse** (`IfcMappedItem` / `IfcRepresentationMap`) — 500 identical doors reference one geometry definition

ODA's BimRv SDK uses its Facet Modeler, which tends to emit:

- **Faceted boundary representations** (`IfcFacetedBrep`) — dense triangulated meshes instead of parametric solids
- **Per-instance geometry** — each element potentially carries its own full geometry definition

This is why the same model is 60 MB from cad2data vs 25 MB from Revit's exporter. The geometry is valid (BIMvision proves this), just less efficiently encoded.

### Why walls show "excessive voids"

cad2data creates individual `IfcRelVoidsElement` relationships for each door/window opening in a wall. Some walls in a typical hotel or residential model can have 10, 15, or even 24 openings. Bonsai has a threshold for boolean operations per element — when exceeded, it skips the void cuts and renders the wall as a solid shape without door/window holes.

This is a **display issue**, not a data issue. The IFC data (doors, windows, their relationships to walls) is intact and queryable. The openings just aren't visually cut into the wall geometry in Bonsai's viewport.

> **Correction, measured against the test model.** The cad2data output examined for 0.0.3 contains **zero** `IfcOpeningElement` and **zero** `IfcRelVoidsElement` — the door and window holes are baked directly into tessellated geometry, so there is no boolean for Bonsai to skip and the warning above cannot arise from that file. Its walls are `IfcPolygonalFaceSet`, not `IfcFacetedBrep` as stated in the section above. The excessive-void warnings originally attributed to cad2data came from a *different export of the same building* imported in the same Blender session; the GlobalIds match because they originate in Revit and survive both exporters. Whether other cad2data conversions emit real void relationships is untested — treat this section as unverified until a file that reproduces it is available.

---

## What the Script Does

### Overview

The script performs six fixes and two diagnostic passes:

1. **Fix orphan IfcSpace aggregation** — Finds every `IfcSpace` that has no parent via `IfcRelAggregates` and connects it to the appropriate `IfcBuildingStorey` (matched by absolute elevation when possible, fallback to lowest storey). Reports the resulting per-storey distribution.

2. **Fix broken element containment chains** — For every `IfcElement`, walks the full containment chain to verify it resolves to an `IfcBuildingStorey`. Elements whose chain is broken (either no container at all, or contained in an orphan `IfcSpace`) get reassigned directly to a storey.

3. **Remove null containment relationships** — Deletes any `IfcRelContainedInSpatialStructure` where `RelatingStructure` is `None`.

4. **Remove null aggregation relationships** — Deletes any `IfcRelAggregates` where `RelatingObject` is `None`.

5. **Fix IfcShapeAspect.ProductDefinitional** — Sets the required `ProductDefinitional` attribute to `False` on entities where it's missing. This is a separate ODA issue that prevents IfcOpenShell's `Optimise` recipe from running.

6. **Blank the converter's header attribution** — cad2data writes its vendor name into the STEP header's `organization` and `authorization` fields, which Bonsai shows as Organisation and Authoriser. Both are cleared.

7. **Verification pass** — After all fixes, re-walks every element's containment chain to confirm no broken chains remain.

8. **Void diagnostic** — Reports on void relationships (count, distribution) without modifying them.

### Detailed walkthrough

#### Fix 1: IfcSpace aggregation

This is the critical fix that resolves the crash.

```python
for space in f.by_type("IfcSpace"):
    parent = ifcopenshell.util.element.get_aggregate(space)
    if parent is None:
        # This IfcSpace is an orphan — find the right storey
        best_storey = find_storey_by_elevation(space, storeys, fallback)
        ifcopenshell.api.run("aggregate.assign_object", f,
            products=[space],
            relating_object=best_storey)
```

`aggregate.assign_object` creates the missing `IfcRelAggregates` relationship, connecting the `IfcSpace` to a storey. After this, Bonsai's `while container.is_a("IfcSpace")` loop can walk up from the space to its parent storey without hitting `None`.

**Storey matching by elevation:** The script finds the correct storey by comparing the IfcSpace's **absolute** Z against storey elevations, picking the highest storey whose elevation is at or below the space. If the position can't be resolved, it falls back to the lowest-elevation storey in the file.

The word *absolute* is doing real work here. `IfcLocalPlacement.RelativePlacement.Location` gives coordinates **relative to the parent placement**, and cad2data leaves that at the origin for IfcSpaces — so reading it directly returns `Z = 0` for every space in the model. Compared against real storey elevations, `0` matches only a below-grade storey, and every room in the building gets aggregated into the foundation level. The repair "succeeds", the verification pass reports clean chains, Bonsai imports without error — and all the rooms are in the wrong collection.

`ifcopenshell.util.placement.get_local_placement()` resolves the full placement chain and returns the absolute transform, which is what makes the comparison meaningful:

```python
z = ifcopenshell.util.placement.get_local_placement(space.ObjectPlacement)[2][3]
```

On the 11-storey hotel used for testing, this is the difference between all 124 spaces landing on the foundation level and distributing correctly across the three levels that actually contain rooms. Fixed in 0.0.3; see [Version history](#version-history).

#### Fix 2: Broken element chains

Even after Fix 1, some elements may have broken chains due to other defects. This pass catches them:

```python
for element in f.by_type("IfcElement"):
    container = get_container(element)
    if container is None:
        # No container at all — assign to storey
        reassign_to_storey(element, storeys, fallback)
    elif container.is_a("IfcSpace"):
        # Verify the IfcSpace chain resolves to a storey
        storey = find_parent_storey(container, f)
        if storey is None:
            # Chain is still broken — bypass IfcSpace, go directly to storey
            reassign_to_storey(element, storeys, fallback)
```

The `find_parent_storey` function walks both aggregation and containment relationships upward, with cycle detection, until it finds an `IfcBuildingStorey` or exhausts all paths.

#### Fix 3 & 4: Null relationships

Some `IfcRelContainedInSpatialStructure` and `IfcRelAggregates` entities in the file have `None` as their relating object. These are invalid per the IFC schema and cause various downstream failures. The script simply removes them.

#### Fix 5: IfcShapeAspect

The `ProductDefinitional` attribute is required (not optional) on `IfcShapeAspect` in IFC4. ODA's writer leaves it as `None` on some entities. This doesn't affect Bonsai import, but it crashes IfcOpenShell's `Optimise` recipe when it tries to copy these entities to a new file. Setting it to `False` (the conservative default — "this shape aspect is not defining the product shape") resolves the issue.

#### Fix 6: File header attribution

The `FILE_NAME` entry in the STEP header carries the vendor name in two places:

```
FILE_NAME('0001','...',('User'),('DataDrivenConstruction'),'ODA SDAI 27.2',$,'DataDrivenConstruction');
                                  ^ organization                             ^ authorization
```

Bonsai surfaces these as **Organisation** and **Authoriser** in Project Info. Those fields describe who authored and signed off the data — they are not the place to advertise the converter. Both are set to blank.

**The script blanks these fields; it never substitutes another name.** It does not write in the name of whoever runs it. Someone repairing an IFC is usually not the author of the building model — they received the file from whoever designed it — so the honest state is empty, leaving the fields available for whoever actually holds authorship to fill in. Writing any name there automatically would recreate the same false claim this fix removes.

What is deliberately left alone: `preprocessor_version` (`ODA SDAI 27.2`) and the `IfcApplication` / `IfcOrganization` entities naming `RVT2IFCconverter`. Those exist precisely to record which tool produced the file, and stripping them would misrepresent its provenance.

---

## Pipeline

### Prerequisites

```bash
pip install ifcopenshell ifcpatch
```

- Python 3.10+ (tested with 3.13 and 3.14)
- IfcOpenShell 0.8.5
- cad2data Community Edition (Windows — the converter itself)
- Bonsai BIM (Blender 4.2 LTS or Blender 5.x)

### Steps

```
# 1. Convert Revit to IFC (Windows only — cad2data is a Windows binary)
RVT2IFCconverter.exe input.rvt -o output.ifc

# 2. Repair for Bonsai
python fix_cad2data.py output.ifc output_fixed.ifc

# 3. (Optional) Optimize file size
python -m ifcpatch -i output_fixed.ifc -o output_optimized.ifc -r Optimise

# 4. Open in Bonsai
# File → Open IFC Project → output_fixed.ifc (or output_optimized.ifc)
```

### What you get

After the repair, the IFC file opens in Bonsai with:

- All geometry visible and correctly placed
- Full IFC class assignments (IfcWall, IfcDoor, IfcSlab, etc.)
- Spatial hierarchy intact (Site → Building → Storeys → Elements)
- Properties and property sets preserved
- Types (IfcWallType, etc.) preserved
- Material assignments preserved

### Known limitations

- **Excessive void warnings** — Walls with many openings (common in hotels, dorms, hospitals) will render without their door/window boolean cuts. The IFC data is intact; the visual cuts are skipped by Bonsai's importer for performance. This is a Bonsai-side threshold, not a file defect.

- **File size** — The repaired file retains cad2data's faceted geometry. Running `ifcpatch -r Optimise` after the repair may reduce size by deduplicating shared geometry, but won't match Revit's native exporter output (which uses parametric solids and IfcMappedItem reuse).

- **Windows only for conversion** — cad2data's `RVT2IFCconverter.exe` is a Windows binary wrapping ODA's proprietary SDK. The repair script itself runs on any platform with Python + IfcOpenShell.

- **Revit version coverage** — cad2data supports Revit 2015–2026 files. Older formats are not supported by the underlying ODA BimRv SDK.

- **Not buildingSMART certified** — cad2data's IFC output is not certified by buildingSMART. ODA's own FAQ states certification is planned but not yet achieved. The repair script fixes the known structural defects but cannot guarantee schema compliance for all edge cases.

---

## Technical Context

### Why this defect exists

The cad2data converter wraps the Open Design Alliance (ODA) BimRv SDK, a commercial library that reverse-engineers Revit's proprietary `.rvt` binary format (a Microsoft Compound File / OLE container). ODA reads the element database and cached geometry from the Revit file and maps them to IFC entities.

The defect — missing `IfcRelAggregates` for IfcSpaces — suggests that ODA's IFC writer correctly handles the "element → spatial container" relationship (IfcRelContainedInSpatialStructure) but does not create the "spatial container → parent spatial container" relationship (IfcRelAggregates) for IfcSpaces. This is likely because:

1. Revit's internal model stores rooms and spaces differently from the IFC spatial hierarchy
2. The mapping from Revit's room/space model to IFC's IfcRelAggregates is incomplete in ODA's writer
3. Since ODA's own viewers (OpenIFCViewer, BricsCAD) don't require complete aggregation chains, the defect was never caught in their testing

Revit's own IFC exporter (the open-source [revit-ifc](https://github.com/Autodesk/revit-ifc) project) handles this correctly because it has full access to Revit's API and was specifically built and certified for IFC interoperability.

### Affected tools

| Tool | Behavior with cad2data IFC |
|---|---|
| BIMvision | Opens correctly — doesn't walk aggregation chains |
| FreeCAD (NativeIFC) | Opens correctly — renders geometry directly from IFC |
| FreeCAD (Import) | Partial import — some spatial data lost |
| IfcOpenShell (ifcconvert) | Converts geometry correctly — doesn't process collections |
| Bonsai BIM | **Crashes** — requires complete aggregation chain for collection assignment |
| Solibri, Navisworks | Untested — likely depends on how they process spatial hierarchy |

### IfcOpenShell API functions used

| Function | Purpose in script |
|---|---|
| `ifcopenshell.open()` | Read IFC file |
| `ifcopenshell.util.element.get_container()` | Find element's spatial container via `IfcRelContainedInSpatialStructure` |
| `ifcopenshell.util.element.get_aggregate()` | Find spatial element's parent via `IfcRelAggregates` |
| `ifcopenshell.api.run("aggregate.assign_object")` | Create missing `IfcRelAggregates` relationship |
| `ifcopenshell.api.run("spatial.assign_container")` | Create/update `IfcRelContainedInSpatialStructure` |
| `f.remove()` | Delete invalid relationship entities |
| `f.write()` | Save repaired file |

---

## Relationship to the IFC Ecosystem

### IFC schema references

- **IfcRelAggregates** — IFC4 §5.1.3.1 — Decomposes a spatial structure element into parts. This is the relationship type cad2data fails to create for IfcSpaces.
- **IfcRelContainedInSpatialStructure** — IFC4 §5.1.3.5 — Contains elements within a spatial structure element. cad2data creates these correctly.
- **IfcBuildingStorey** — IFC4 §5.1.2.2 — The spatial structure element that represents a floor level. The target of the missing aggregation.
- **IfcSpace** — IFC4 §5.1.2.4 — A spatial element representing a room or zone. The orphaned entity in cad2data output.
- **IfcShapeAspect** — IFC4 §8.11.3.2 — `ProductDefinitional` is a required BOOLEAN attribute. ODA leaves it null.

### Related IfcOpenShell tools

- **ifcpatch Optimise** — Deduplicates shared geometry definitions. Fails on cad2data files due to the IfcShapeAspect defect (fixed by this script).
- **ifcpatch TessellateElements** — Re-tessellates geometry through IfcOpenShell's Python bindings. Does not fix the crash because the crash is in spatial structure, not geometry.
- **ifcconvert** — Converts IFC to OBJ/DAE/other formats. Works correctly on cad2data files because it processes geometry only, not spatial hierarchy.
- **ifcopenshell.validate** — Schema validation module. Can detect the missing `ProductDefinitional` attribute but not the semantic defect of orphaned IfcSpaces (which is structurally valid per schema — just missing expected relationships).

---

## Development Notes

### Testing

To verify the fix works:

1. Convert any Revit file (2015–2026) using cad2data
2. Attempt to open the resulting IFC in Bonsai — confirm it crashes
3. Run `fix_cad2data.py` on the file
4. Open the fixed file in Bonsai — confirm it loads without crash
5. Verify in Bonsai's spatial decomposition panel that all elements are correctly assigned to storeys

### Edge cases to investigate

- **Multi-building files** — Does cad2data correctly separate elements across multiple IfcBuilding entities? The script's fallback storey selection may need to account for this.
- **Linked models** — The Bonsai console log suggests the hotel file contained linked/referenced models. Each linked model may need independent repair.
- **IfcSpace boundaries** — The script does not check `IfcRelSpaceBoundary` relationships. These define which elements bound a space (walls, floors, ceilings). If cad2data also fails to create these, space-based analysis (energy, room finish schedules) will be incomplete.
- **Storey elevation accuracy** — The elevation matching uses a simple "highest storey at or below the element's absolute Z" heuristic. It resolves the full placement chain (fixed in 0.0.3), but the heuristic itself may still misassign elements in buildings with mezzanines, split levels, or non-standard storey heights.
- **Silent misassignment** — The verification pass confirms every containment chain *resolves*, not that it resolves *correctly*. A file can pass verification, import cleanly into Bonsai, and still have elements in the wrong storey. Check the spatial decomposition panel after import; the script now prints the per-storey distribution of aggregated spaces so an obviously wrong result is visible in the console.
- **IFC2x3 vs IFC4** — cad2data can export both. The script uses IfcOpenShell's API which handles both schemas, but the spatial hierarchy structure differs slightly between them. Testing with IFC2x3 output is recommended.

### Potential improvements

- **Batch processing** — Accept a directory of IFC files and process all of them.
- **Validation report** — Output a structured report (JSON, CSV) of what was fixed, for integration into audit workflows.
- **Void threshold configuration** — Allow users to set Bonsai's excessive-void threshold or pre-merge voids in the file to avoid the warning.
- **Geometry optimization** — Detect and merge duplicate geometry definitions that cad2data creates (what `ifcpatch Optimise` does, but without the IfcShapeAspect crash).
- **CI integration** — Run as a GitHub Action that automatically repairs IFC files in a repository.
- **Comparison with Revit export** — Given the same .rvt source, compare cad2data output vs Revit native export and report differences in entity counts, geometry types, and spatial structure completeness.

---

## Context: The No-Revit Pipeline

This tool exists to serve architects and BIM professionals who receive `.rvt` files but do not have a Revit license. The complete pipeline:

```
.rvt file (from collaborator)
    │
    ▼
cad2data RVT2IFCconverter.exe     ← Free, offline, local
    │
    ▼
Raw IFC (broken spatial hierarchy)
    │
    ▼
fix_cad2data.py                    ← This script
    │
    ▼
Repaired IFC
    │
    ▼
Bonsai BIM (Blender)               ← Free, open source
    │
    ▼
Documentation, audit, coordination
```

Every step runs locally. No cloud accounts, no Autodesk subscription, no uploads. The model never leaves the machine — relevant for projects with confidentiality requirements.

---

## Version history

The script prints its version at startup, so console output can be traced back to the code that produced it.

### 0.0.4

- Added Fix 6: blank the `organization` and `authorization` fields in the STEP header, which cad2data stamps with its vendor name and Bonsai displays as Organisation and Authoriser. The converter's own identification in `preprocessor_version` and `IfcApplication` is left intact.

### 0.0.3

- **Fixed: every IfcSpace was assigned to the wrong storey.** Elevation matching read `ObjectPlacement.RelativePlacement.Location`, which is relative to the parent placement and is `0` for cad2data's IfcSpaces. Every space therefore matched only a below-grade storey. Now resolved through `ifcopenshell.util.placement.get_local_placement()`. On the test model this moved 124 spaces off the foundation level and onto the three levels that contain them.
- Storey selection now takes the *highest* qualifying storey rather than the last one encountered in file order. The two agreed whenever `by_type()` happened to return storeys in elevation order, and disagreed when it didn't.
- Fix 1 now prints the per-storey distribution of aggregated spaces, and warns when every space lands on a single storey — the visible symptom of the bug above.
- Both elevation call sites share one `find_storey_by_elevation()` helper.
- Corrected the usage string, which named a filename that doesn't exist.

### 0.0.2

- First versioned release. Behaviour unchanged from the original script; added version reporting and a status note on testing coverage.

## License

GPL-3.0 — see [LICENSE](LICENSE).

## Credits

- [cad2data](https://github.com/datadrivenconstruction/cad2data-Revit-IFC-DWG-DGN) by DataDrivenConstruction (Artem Boiko) — Revit-to-IFC conversion
- [IfcOpenShell](https://ifcopenshell.org) — IFC processing library
- [Bonsai BIM](https://bonsaibim.org) — Blender BIM add-on
- Diagnostic methodology developed through systematic testing of cad2data output against multiple IFC viewers
