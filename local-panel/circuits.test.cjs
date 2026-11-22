"use strict";

const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const { test } = require("node:test");
const { type, license, features } = require("./circuits.json");
const { project, pointAtLap } = require("./circuit-map.js");

const lengths = { interlagos: 4.309, "las-vegas": 6.201, lusail: 5.419, "yas-marina": 5.281 };
const finishBearings = { interlagos: 165, "las-vegas": 42, lusail: 331, "yas-marina": 82 };
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
    const route = project({ geometry, properties });
    const [x, y] = pointAtLap(route, 0);
    assert.deepEqual(pointAtLap(route, 1), [x, y], id);
    const [aheadX, aheadY] = pointAtLap(route, .001);
    const bearing = Math.atan2(aheadX - x, aheadY - y);
    assert(Math.cos(bearing - radians(finishBearings[id])) > Math.cos(radians(8)), `${id}: sentido en meta`);
  }
});

test("lap fractions start at the timing line and follow distance, not vertex count", () => {
  const geometry = { type: "LineString", coordinates: [[0, -1], [8, -1], [8, 1], [3, 1], [0, 1], [0, -1]] };
  for (const direction of [1, -1]) {
    const route = project({ geometry, properties: { osm: "way/1", timing_line: [0, .5], direction } });
    assert.deepEqual(pointAtLap(route, 0), [0, -.25]);
    assert.deepEqual(pointAtLap(route, .25), [direction, 0]);
    assert.deepEqual(pointAtLap(route, 1), [0, -.25]);
  }
});
