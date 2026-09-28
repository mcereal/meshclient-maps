// Writes build/style/<flavor>/style.json from Protomaps' basemap layers.
//
// The layers are Protomaps' own. What is ours is the wiring tileserver-gl needs (local
// glyphs and sprite, the pmtiles source) and, later, any change that makes the style read better
// on the Brick's 1024x768 panel. Such a change belongs in `tune()` below, not in a fork of the layers.
import { mkdirSync, writeFileSync, copyFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { layers, namedFlavor } from "@protomaps/basemaps";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const flavors = process.argv.slice(2);
if (flavors.length === 0) flavors.push("light");

function tune(styleLayers) {
  return styleLayers;
}

for (const flavor of flavors) {
  const out = join(root, "build", "style", flavor);
  mkdirSync(out, { recursive: true });
  const style = {
    version: 8,
    glyphs: "{fontstack}/{range}.pbf",
    sprite: "{styleJsonFolder}/sprite",
    sources: {
      protomaps: {
        type: "vector",
        url: "pmtiles://{protomaps}",
        attribution: "© OpenStreetMap contributors",
      },
    },
    layers: tune(layers("protomaps", namedFlavor(flavor), { lang: "en" })),
  };
  writeFileSync(join(out, "style.json"), JSON.stringify(style));
  const sprites = join(root, "build", "assets", "sprites", "v4");
  for (const suffix of [".json", ".png", "@2x.json", "@2x.png"]) {
    copyFileSync(join(sprites, flavor + suffix), join(out, "sprite" + suffix));
  }
  console.log(`build/style/${flavor}/style.json`);
}
