# MMDL Blender Toolkit

Blender add-on for importing and inspecting model assets from **Shutokou Battle Online / 首都高バトルONLINE** game data. It loads MMDL geometry, MIA textures, PRTS object-slot mappings, and game-specific car-part metadata directly into Blender.

The project currently focuses on accurate importing and format research. MMDL/MIA export and conversion from other model formats are planned but are not implemented yet.

## Requirements

- Blender 3.6 or newer
- Blender 5.2 is used for current development and testing
- Original game assets are not included in this repository

## Installation

1. Download or build `sb_online_toolkit-<version>.zip`.
2. In Blender, open **Edit > Preferences > Add-ons**.
3. Choose **Install from Disk** and select the ZIP without extracting it.
4. Enable **SB Online Toolkit**.
5. Import a model through **File > Import > SBOL MMDL (.mmdl)**.

When upgrading the add-on, reimport the MMDL. Materials, shader nodes, PRTS metadata, and object properties are constructed during import.

## Asset layout

The importer supports the original game-style hierarchy:

```text
data/
|-- car/
|   |-- MODEL.mmdl
|   `-- PRTS/
|       `-- MODEL.PRTS
`-- tex/
    |-- car/
    |   `-- MODEL.MIA
    `-- cn/
        `-- CN_MODEL.MIA
```

PRTS files can also be placed directly beside their matching MMDL.

## Current features

### MMDL import

- Supports 24-byte and 36-byte vertex records
- Imports positions, normals, UVs, packed vertex colours, triangle indices, objects, and material groups
- Converts the game's Y-up coordinate system to Blender's Z-up layout
- Preserves object-table, parent-record, material, and serialized-pointer metadata for diagnostics and future round-trip export
- Validates the confirmed `0x48`, `0x28`, `0x88`, and `0xA0` object-table structures
- Imports LOD A as the visible high-detail model
- Imports LOD B, LOD C, and Auxiliary D hidden by default
- Keeps collision geometry hidden by default

### MIA textures and materials

- Reads raw and RLE BGRA MIA cabinets
- Indexes shared archives quickly and decodes only textures used by the imported model
- Supports texture alpha, masked materials, projected-light black-key transparency, and emissive light textures
- Recognises game glass naming including `GLASS`, `GLAS`, `GRASS`, `MADO`, and pane/window variants
- Applies stored opacity to overlapping inner and outer glass layers

### PRTS and car parts

- Decodes PRTS as four 240-slot canonical object-mapping tables
- Associates MMDL parent/child records with their canonical PRTS slots
- Uses PRTS slots for part-stage resolution where available
- Provides controls for body/interior, mufflers, overfenders, bumpers, bonnets, mirrors, side skirts, spoilers, grilles, and lights
- Treats Stock as no additional mesh for overfenders and interior/body upgrades
- Keeps interior/body upgrade stages mutually exclusive
- Handles number plates, collision meshes, shared light effects, and known CARMODS conventions

### Blender tools

- Two-colour car-paint preview using confirmed material flag selectors
- CARMODS parts browser
- Object and material inspectors
- PRTS mapping information
- Diagnostic JSON export containing parsed model metadata

## Building

Run the build script from the repository root and provide a semantic version:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -Version 1.4.12
```

The script:

- Updates the version in `bl_info` and `sb_online_toolkit/README.txt`
- Packages the stable `sb_online_toolkit` module folder
- Excludes Python caches
- Writes `dist/sb_online_toolkit-<version>.zip`

## Repository layout

```text
sb_online_toolkit/   Blender add-on source
build.ps1            Versioning and packaging script
dist/                Generated installation ZIPs (ignored)
CAR/                  Local MMDL/PRTS research assets (ignored)
TEX/                  Local MIA research assets (ignored)
```

## Known limitations and roadmap

- PRTS provides canonical object mapping, while some category/stage ranges still rely on observed game conventions.
- Several material flag bits remain unidentified, although colour-channel selectors and several rendering behaviours are confirmed.
- Some spatial fields appear to contain object and model bounds but are not yet regenerated.
- Writing MMDL, MIA, and PRTS files is still under development.
- Conversion from OBJ, FBX, or glTF into game-ready MMDL is not yet available.

The intended next milestones are lossless imported-MMDL round trips, MIA writing, and validated conversion from ordinary Blender meshes into game-compatible assets.
