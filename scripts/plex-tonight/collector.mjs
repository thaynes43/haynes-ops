const HISTORY_PAGE_SIZE = 1000;
const PLEX_PAGE_SIZE = 500;
const DETAIL_CONCURRENCY = 4;
const REQUEST_TIMEOUT_MS = 30_000;

const HISTORY_FIELDS = Object.freeze([
  "date",
  "media_type",
  "rating_key",
  "parent_rating_key",
  "grandparent_rating_key",
  "full_title",
  "title",
  "grandparent_title",
  "year",
  "media_index",
  "parent_media_index",
  "guid",
  "percent_complete",
  "watched_status",
  "duration",
]);

const SERVER_DEFINITIONS = Object.freeze([
  {
    id: "haynesops",
    tautulliUrl: "http://tautulli.media.svc.cluster.local:8181",
    tautulliKeyEnv: "TAUTULLI_API_KEY",
    plexUrl: "http://plexops.media.svc.cluster.local:32400",
    plexTokenEnv: "PLEX_HAYNESOPS_TOKEN",
    entertainmentLibraries: Object.freeze(["HOps Movies", "HOps TV Shows"]),
  },
  {
    id: "hayneskube",
    tautulliUrl: "http://tautulli-k8plex.media.svc.cluster.local:8181",
    tautulliKeyEnv: "TAUTULLI_K8PLEX_API_KEY",
    plexUrl: "http://plex.media.svc.cluster.local:32400",
    plexTokenEnv: "PLEX_HAYNESKUBE_TOKEN",
    entertainmentLibraries: Object.freeze([]),
  },
  {
    id: "haynestower",
    tautulliUrlEnv: "TAUTULLI_HAYNESTOWER_URL",
    tautulliKeyEnv: "TAUTULLI_HAYNESTOWER_API_KEY",
    plexUrl: "https://plex.haynesnetwork.com",
    plexTokenEnv: "PLEX_HAYNESTOWER_TOKEN",
    entertainmentLibraries: Object.freeze(["HNet Movies", "HNet TV Shows"]),
  },
]);

class CollectorError extends Error {}

function requiredEnvironment(name, serverId) {
  const value = process.env[name];
  if (!value) {
    throw new CollectorError(`${serverId}: required application environment is unavailable`);
  }
  return value;
}

function serverRuntimeConfig(definition) {
  return {
    ...definition,
    tautulliUrl: definition.tautulliUrl
      ?? requiredEnvironment(definition.tautulliUrlEnv, definition.id),
    tautulliKey: requiredEnvironment(definition.tautulliKeyEnv, definition.id),
    plexToken: requiredEnvironment(definition.plexTokenEnv, definition.id),
  };
}

function buildUrl(baseUrl, path, query = {}) {
  let url;
  try {
    url = new URL(path, `${baseUrl.replace(/\/$/, "")}/`);
  } catch {
    throw new CollectorError("collector endpoint configuration is invalid");
  }
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
  }
  return url;
}

async function fetchJson({ baseUrl, path, query, headers, serverId, operation }) {
  const url = buildUrl(baseUrl, path, query);
  let response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers,
      redirect: "error",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    throw new CollectorError(`${serverId}: ${operation} request failed`);
  }

  if (!response.ok) {
    throw new CollectorError(`${serverId}: ${operation} returned HTTP ${response.status}`);
  }

  try {
    return await response.json();
  } catch {
    throw new CollectorError(`${serverId}: ${operation} returned invalid JSON`);
  }
}

function tautulliData(config, parameters, operation) {
  return fetchJson({
    baseUrl: config.tautulliUrl,
    path: "/api/v2",
    query: { apikey: config.tautulliKey, ...parameters },
    serverId: config.id,
    operation,
  }).then((payload) => {
    if (payload?.response?.result !== "success" || payload.response.data == null) {
      throw new CollectorError(`${config.id}: ${operation} returned an unsuccessful response`);
    }
    return payload.response.data;
  });
}

function plexData(config, path, query, operation) {
  return fetchJson({
    baseUrl: config.plexUrl,
    path,
    query,
    headers: {
      Accept: "application/json",
      "X-Plex-Token": config.plexToken,
    },
    serverId: config.id,
    operation,
  });
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function numberOrNull(value) {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function stringOrNull(value) {
  if (value === undefined || value === null || value === "") return null;
  return String(value);
}

export function isolateHistoryRecord(record) {
  const isolated = {};
  for (const field of HISTORY_FIELDS) {
    if (Object.hasOwn(record, field)) isolated[field] = record[field];
  }
  return isolated;
}

export function isolateOwnerHistory(rows, ownerId) {
  if (rows.some((row) => String(row.user_id ?? "") !== String(ownerId))) {
    throw new CollectorError("history response included a record for another user");
  }
  return rows
    .filter((row) => row.media_type === "movie" || row.media_type === "episode")
    .map(isolateHistoryRecord);
}

export async function fetchAllHistory(fetchPage, userId, pageSize = HISTORY_PAGE_SIZE) {
  const rows = [];
  let start = 0;
  let recordsFiltered = null;

  for (;;) {
    const page = await fetchPage({
      cmd: "get_history",
      user_id: userId,
      grouping: 0,
      order_column: "date",
      order_dir: "desc",
      start,
      length: pageSize,
    });
    const pageRows = asArray(page?.data);
    const reportedTotal = numberOrNull(page?.recordsFiltered);
    if (reportedTotal === null || reportedTotal < 0) {
      throw new CollectorError("history response is missing its filtered total");
    }
    recordsFiltered = reportedTotal;
    rows.push(...pageRows);

    if (rows.length >= recordsFiltered) break;
    if (pageRows.length === 0) {
      throw new CollectorError("history pagination ended before its filtered total");
    }
    start += pageRows.length;
  }

  return { recordsFiltered: recordsFiltered ?? 0, rows };
}

export async function fetchAllPlexMetadata(fetchPage, pageSize = PLEX_PAGE_SIZE) {
  const items = [];
  let start = 0;
  let totalSize = null;

  for (;;) {
    const payload = await fetchPage({ start, size: pageSize });
    const container = payload?.MediaContainer;
    if (!container || typeof container !== "object") {
      throw new CollectorError("Plex response is missing its media container");
    }
    const pageItems = asArray(container.Metadata);
    const reportedTotal = numberOrNull(container.totalSize);
    if (reportedTotal !== null && reportedTotal >= 0) totalSize = reportedTotal;
    items.push(...pageItems);

    if (totalSize !== null && items.length >= totalSize) break;
    if (pageItems.length === 0) {
      if (totalSize === null || items.length === totalSize) break;
      throw new CollectorError("Plex pagination ended before its reported total");
    }
    start += pageItems.length;
    if (totalSize === null && pageItems.length < pageSize) break;
  }

  return { totalSize: totalSize ?? items.length, items };
}

function compactMedia(media) {
  return asArray(media).map((variant) => ({
    videoResolution: stringOrNull(variant.videoResolution),
    width: numberOrNull(variant.width),
    height: numberOrNull(variant.height),
    videoCodec: stringOrNull(variant.videoCodec),
    audioCodec: stringOrNull(variant.audioCodec),
    container: stringOrNull(variant.container),
    bitrate: numberOrNull(variant.bitrate),
    duration: numberOrNull(variant.duration),
    parts: asArray(variant.Part).map((part) => ({
      size: numberOrNull(part.size),
      duration: numberOrNull(part.duration),
      container: stringOrNull(part.container),
    })),
  }));
}

function commonMetadata(item) {
  return {
    ratingKey: stringOrNull(item.ratingKey),
    title: stringOrNull(item.title),
    year: numberOrNull(item.year),
    summary: stringOrNull(item.summary),
    duration: numberOrNull(item.duration),
    originallyAvailableAt: stringOrNull(item.originallyAvailableAt),
    contentRating: stringOrNull(item.contentRating),
    rating: numberOrNull(item.rating),
    audienceRating: numberOrNull(item.audienceRating),
    guid: stringOrNull(item.guid),
    guids: asArray(item.Guid).map((entry) => stringOrNull(entry?.id)).filter((value) => value !== null),
    genres: asArray(item.Genre).map((entry) => stringOrNull(entry?.tag)).filter((value) => value !== null),
  };
}

function movieMetadata(item) {
  return {
    ...commonMetadata(item),
    tagline: stringOrNull(item.tagline),
    viewCount: numberOrNull(item.viewCount),
    lastViewedAt: numberOrNull(item.lastViewedAt),
    viewOffset: numberOrNull(item.viewOffset),
    media: compactMedia(item.Media),
  };
}

function showMetadata(item) {
  return {
    ...commonMetadata(item),
    childCount: numberOrNull(item.childCount),
    leafCount: numberOrNull(item.leafCount),
    viewedLeafCount: numberOrNull(item.viewedLeafCount),
    viewCount: numberOrNull(item.viewCount),
    lastViewedAt: numberOrNull(item.lastViewedAt),
    viewOffset: numberOrNull(item.viewOffset),
  };
}

function episodeMetadata(item) {
  return {
    ...commonMetadata(item),
    parentRatingKey: stringOrNull(item.parentRatingKey),
    grandparentRatingKey: stringOrNull(item.grandparentRatingKey),
    parentTitle: stringOrNull(item.parentTitle),
    grandparentTitle: stringOrNull(item.grandparentTitle),
    parentIndex: numberOrNull(item.parentIndex),
    index: numberOrNull(item.index),
    viewCount: numberOrNull(item.viewCount),
    lastViewedAt: numberOrNull(item.lastViewedAt),
    viewOffset: numberOrNull(item.viewOffset),
    media: compactMedia(item.Media),
  };
}

export function resolveSoleOwnerId(serverId, users) {
  const userRows = Array.isArray(users) ? users : asArray(users?.users);
  const admins = userRows.filter((user) => user.is_admin === true || Number(user.is_admin) === 1);
  const ownerId = admins.length === 1 ? stringOrNull(admins[0].user_id) : null;
  if (ownerId === null) {
    throw new CollectorError(`${serverId}: exactly one Tautulli owner was not found`);
  }
  return ownerId;
}

async function mapWithConcurrency(values, concurrency, transform) {
  const results = new Array(values.length);
  let nextIndex = 0;
  async function worker() {
    for (;;) {
      const index = nextIndex++;
      if (index >= values.length) return;
      results[index] = await transform(values[index], index);
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, values.length) }, worker));
  return results;
}

function historyShowKeys(historyRows) {
  return new Set(
    historyRows
      .filter((row) => row.media_type === "episode" && row.grandparent_rating_key != null)
      .map((row) => String(row.grandparent_rating_key)),
  );
}

function showHasOwnerActivity(show, playedShowKeys) {
  return (numberOrNull(show.viewedLeafCount) ?? 0) > 0
    || playedShowKeys.has(String(show.ratingKey));
}

async function collectHistory(config, ownerId) {
  const result = await fetchAllHistory(
    (parameters) => tautulliData(config, parameters, "history"),
    ownerId,
  );
  const records = isolateOwnerHistory(result.rows, ownerId);
  return {
    recordsFiltered: result.recordsFiltered,
    includedRecords: records.length,
    records,
  };
}

async function collectMachineIdentifier(config) {
  const payload = await plexData(config, "/identity", {}, "server identity");
  const machineIdentifier = stringOrNull(payload?.MediaContainer?.machineIdentifier);
  if (machineIdentifier === null) {
    throw new CollectorError(`${config.id}: Plex identity is missing its machine identifier`);
  }
  return machineIdentifier;
}

async function collectLibraryItems(config, section, typeCode) {
  return fetchAllPlexMetadata(({ start, size }) => plexData(
    config,
    `/library/sections/${encodeURIComponent(section.key)}/all`,
    {
      type: typeCode,
      includeGuids: 1,
      "X-Plex-Container-Start": start,
      "X-Plex-Container-Size": size,
    },
    `${section.type} catalog`,
  ));
}

async function collectShowEpisodes(config, show) {
  const result = await fetchAllPlexMetadata(({ start, size }) => plexData(
    config,
    `/library/metadata/${encodeURIComponent(show.ratingKey)}/allLeaves`,
    {
      includeGuids: 1,
      "X-Plex-Container-Start": start,
      "X-Plex-Container-Size": size,
    },
    "show episode details",
  ));
  return result.items.map(episodeMetadata);
}

async function collectCatalog(config, historyRows) {
  const [machineIdentifier, sectionsPayload] = await Promise.all([
    collectMachineIdentifier(config),
    plexData(config, "/library/sections", {}, "library list"),
  ]);
  const sections = asArray(sectionsPayload?.MediaContainer?.Directory);
  const expected = new Set(config.entertainmentLibraries);
  const selected = sections.filter((section) => expected.has(section.title));
  if (selected.length !== expected.size) {
    throw new CollectorError(`${config.id}: an expected entertainment library is unavailable`);
  }

  const playedShowKeys = historyShowKeys(historyRows);
  const libraries = [];
  for (const section of selected) {
    if (section.type !== "movie" && section.type !== "show") {
      throw new CollectorError(`${config.id}: an entertainment library has an unexpected type`);
    }
    const result = await collectLibraryItems(config, section, section.type === "movie" ? 1 : 2);
    if (section.type === "movie") {
      const movies = result.items.map(movieMetadata);
      libraries.push({
        key: String(section.key),
        title: String(section.title),
        type: "movie",
        totalSize: result.totalSize,
        movies,
      });
      continue;
    }

    const rawShows = result.items;
    const activeIndexes = rawShows
      .map((show, index) => ({ show, index }))
      .filter(({ show }) => showHasOwnerActivity(show, playedShowKeys));
    const details = await mapWithConcurrency(
      activeIndexes,
      DETAIL_CONCURRENCY,
      ({ show }) => collectShowEpisodes(config, show),
    );
    const detailsByIndex = new Map(activeIndexes.map(({ index }, detailIndex) => [index, details[detailIndex]]));
    const shows = rawShows.map((show, index) => ({
      ...showMetadata(show),
      episodes: detailsByIndex.get(index) ?? null,
    }));
    libraries.push({
      key: String(section.key),
      title: String(section.title),
      type: "show",
      totalSize: result.totalSize,
      showsWithEpisodeDetails: activeIndexes.length,
      shows,
    });
  }
  return { machineIdentifier, libraries };
}

async function collectServer(definition) {
  const config = serverRuntimeConfig(definition);
  const users = await tautulliData(config, { cmd: "get_users" }, "owner lookup");
  const ownerId = resolveSoleOwnerId(config.id, users);
  const history = await collectHistory(config, ownerId);
  const catalog = await collectCatalog(config, history.records);
  return { ownerId, data: { id: config.id, history, catalog } };
}

export async function collectSnapshot() {
  const results = await Promise.all(SERVER_DEFINITIONS.map(collectServer));
  const ownerIds = new Set(results.map((result) => result.ownerId));
  if (ownerIds.size !== 1) {
    throw new CollectorError("Tautulli servers do not agree on the sole owner account");
  }
  return {
    schemaVersion: 1,
    collectedAt: new Date().toISOString(),
    owner: { tautulliUserId: results[0].ownerId },
    servers: results.map((result) => result.data),
  };
}

async function main() {
  try {
    const snapshot = await collectSnapshot();
    process.stdout.write(`${JSON.stringify(snapshot)}\n`);
  } catch (error) {
    const message = error instanceof CollectorError ? error.message : "collector failed unexpectedly";
    process.stderr.write(`plex-tonight: ${message}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] === undefined) await main();
