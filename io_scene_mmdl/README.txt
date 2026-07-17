SBOL MMDL Blender Importer 0.3.0

Install:
1. In Blender, choose Edit > Preferences > Add-ons.
2. Use the drop-down menu and choose Install from Disk.
3. Select io_scene_mmdl.zip.
4. Enable "SBOL MMDL Importer" if Blender does not enable it automatically.
5. Choose File > Import > SBOL MMDL (.mmdl).

Expected SB Online layout:
  <game root>/data/car/*.mmdl
  <game root>/data/tex/car/*.mia
  <game root>/data/tex/cn/*.mia
  <game root>/data/tex/*.mia

MIA search priority:
1. A matching MIA beside the MMDL (development convenience).
2. data/tex/car/<model name>.mia.
3. Matching data/tex/cn/<model name>.mia or CN_<model name>.mia.
4. General/shared MIA files directly in data/tex.
5. Other MIA files in data/tex/cn.

The first matching texture wins, so car-specific textures override shared libraries.
Disable "Search shared MIA libraries" to load only matching car/CN cabinets.

MMDL behaviour deliberately mirrors mmdl2obj.py's proven output:
- X position is negated.
- V texture coordinate is negated.
- Face indices are used as global MMDL indices.
- Object2 becomes a Blender object.
- Object3 entries become material slots.
- Flat shading is used, matching OBJ "s off".

MIA texture support:
- Reads MIA type 0 (raw BGRA) and type 1 (RLE BGRA).
- Texture matching is case-insensitive and ignores common filename extensions.
- Images are created directly in Blender and packed into the .blend file.
- Loose image files remain available as a fallback.
