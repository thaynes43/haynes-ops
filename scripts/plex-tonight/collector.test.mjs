import assert from "node:assert/strict";
import test from "node:test";

import {
  fetchAllHistory,
  fetchAllPlexMetadata,
  isolateHistoryRecord,
  isolateOwnerHistory,
  resolveSoleOwnerId,
} from "./collector.mjs";

test("history pagination advances by received rows and preserves the filtered total", async () => {
  const starts = [];
  const source = [
    { date: 3, title: "Third" },
    { date: 2, title: "Second" },
    { date: 1, title: "First" },
  ];
  const result = await fetchAllHistory(async ({ start, length, grouping }) => {
    starts.push(start);
    assert.equal(length, 1000);
    assert.equal(grouping, 0);
    return { recordsFiltered: "3", data: source.slice(start, start + 2) };
  }, "42");

  assert.deepEqual(starts, [0, 2]);
  assert.equal(result.recordsFiltered, 3);
  assert.deepEqual(result.rows, source);
});

test("history isolation emits only recommendation evidence fields", () => {
  const isolated = isolateHistoryRecord({
    date: 123,
    media_type: "episode",
    title: "A title",
    percent_complete: 72,
    watched_status: 0,
    ip_address: "192.0.2.10",
    player: "Private television",
    user: "private@example.test",
    machine_id: "private-machine-id",
  });

  assert.deepEqual(isolated, {
    date: 123,
    media_type: "episode",
    title: "A title",
    percent_complete: 72,
    watched_status: 0,
  });
  assert.equal(JSON.stringify(isolated).includes("192.0.2.10"), false);
  assert.equal(JSON.stringify(isolated).includes("private@example.test"), false);
});

test("history isolation rejects a row outside the requested owner filter", () => {
  assert.throws(
    () => isolateOwnerHistory([
      { user_id: "42", media_type: "movie", title: "Expected" },
      { user_id: "7", media_type: "movie", title: "Unexpected" },
    ], "42"),
    /another user/,
  );
});

test("owner identity is discovered from exactly one admin", () => {
  const users = [
    { user_id: "7", is_admin: 0 },
    { user_id: "42", is_admin: "1" },
  ];
  assert.equal(resolveSoleOwnerId("test-server", users), "42");
  assert.throws(
    () => resolveSoleOwnerId("test-server", [...users, { user_id: "99", is_admin: 1 }]),
    /exactly one/,
  );
});

test("Plex pagination uses actual page length when a page is shorter than requested", async () => {
  const starts = [];
  const source = Array.from({ length: 5 }, (_, index) => ({ ratingKey: String(index + 1) }));
  const result = await fetchAllPlexMetadata(async ({ start, size }) => {
    starts.push(start);
    assert.equal(size, 500);
    return {
      MediaContainer: {
        totalSize: 5,
        size: Math.min(2, source.length - start),
        offset: start,
        Metadata: source.slice(start, start + 2),
      },
    };
  });

  assert.deepEqual(starts, [0, 2, 4]);
  assert.equal(result.totalSize, 5);
  assert.deepEqual(result.items, source);
});
