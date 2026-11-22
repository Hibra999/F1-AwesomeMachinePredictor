"use strict";

const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const { test } = require("node:test");
const { type, license, features } = require("./circuits.json");

const lengths = { interlagos: 4.309, "las-vegas": 6.201, lusail: 5.419, "yas-marina": 5.281 };
const radians = (degrees) => degrees * Math.PI / 180;

function distance([lon1, lat1], [lon2, lat2]) {
  const sinLat = Math.sin(radians(lat2 - lat1) / 2);
  const sinLon = Math.sin(radians(lon2 - lon1) / 2);
  return 12_742 * Math.asin(Math.sqrt(sinLat ** 2
    + Math.cos(radians(lat1)) * Math.cos(radians(lat2)) * sinLon ** 2));
}

test("the offline ODbL snapshot contains four closed XY circuits of the official approximate length", () => {
  assert.equal(type, "FeatureCollection");
  assert.equal(license, "https://opendatacommons.org/licenses/odbl/1-0/");
  assert.deepEqual(features.map(({ id }) => id), Object.keys(lengths));
  for (const { id, geometry, properties } of features) {
    const { coordinates } = geometry;
    assert.equal(geometry.type, "LineString", id);
    assert.match(properties.osm, /^relation\/\d+$/);
    assert(coordinates.length > 3 && coordinates.every((pair) => pair.length === 2 && pair.every(Number.isFinite)), id);
    assert.deepEqual(coordinates[0], coordinates.at(-1), id);
    const km = coordinates.slice(1).reduce((sum, pair, i) => sum + distance(coordinates[i], pair), 0);
    assert(Math.abs(km / lengths[id] - 1) < .01, `${id}: ${km} km`);
    assert.equal(createHash("sha256").update(JSON.stringify(coordinates)).digest("hex"), properties.coordinates_sha256, id);
  }
});
