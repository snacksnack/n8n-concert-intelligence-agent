#!/usr/bin/env node
/*
  Fetch real QA inputs for local matching checks.

  Required environment:
    SPOTIFY_ACCESS_TOKEN  OAuth token with user-top-read and user-read-recently-played
    TICKETMASTER_API_KEY  Ticketmaster Discovery API key

  Output:
    qa/artists.json
    qa/ticketmaster-response.json
*/
const fs = require('fs');
const path = require('path');

const SPOTIFY_ACCESS_TOKEN = process.env.SPOTIFY_ACCESS_TOKEN;
const TICKETMASTER_API_KEY = process.env.TICKETMASTER_API_KEY;

const OUT_DIR = path.join(process.cwd(), 'qa');
const ARTISTS_OUT = path.join(OUT_DIR, 'artists.json');
const TICKETMASTER_OUT = path.join(OUT_DIR, 'ticketmaster-response.json');

const USER_LAT = '40.6928';
const USER_LNG = '-73.9903';
const RADIUS_MILES = '50';
const MONTHS_AHEAD = 6;
const MAX_PAGES_PER_WINDOW = 5;

function requireEnv(name, value) {
  if (!value) {
    console.error(`Missing ${name}.`);
    process.exitCode = 2;
  }
}

function normalizeName(name) {
  return String(name || '')
    .toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/\s*[\(\[].*?[\)\]]\s*/g, '')
    .replace(/\bfeat\.?\b.*$/i, '')
    .replace(/\bft\.?\b.*$/i, '')
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }

  if (!response.ok) {
    const detail = typeof body === 'string' ? body : JSON.stringify(body);
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }

  return body;
}

async function fetchSpotify(pathname) {
  return fetchJson(`https://api.spotify.com/v1${pathname}`, {
    headers: {
      Authorization: `Bearer ${SPOTIFY_ACCESS_TOKEN}`,
    },
  });
}

function addArtist(artistMap, artist, termKey, rank) {
  if (!artistMap[artist.id]) {
    artistMap[artist.id] = {
      id: artist.id,
      name: artist.name,
      name_normalized: normalizeName(artist.name),
      genres: artist.genres || [],
      short_term_rank: null,
      medium_term_rank: null,
      long_term_rank: null,
      last_played_at: null,
      days_since_played: 999,
      recent_play_count: 0,
    };
  }
  artistMap[artist.id][termKey] = rank;
}

async function buildArtistProfile() {
  const [shortTerm, mediumTerm, longTerm, recent] = await Promise.all([
    fetchSpotify('/me/top/artists?time_range=short_term&limit=50'),
    fetchSpotify('/me/top/artists?time_range=medium_term&limit=50'),
    fetchSpotify('/me/top/artists?time_range=long_term&limit=50'),
    fetchSpotify('/me/player/recently-played?limit=50'),
  ]);

  const artistMap = {};
  shortTerm.items.forEach((artist, index) => addArtist(artistMap, artist, 'short_term_rank', index + 1));
  mediumTerm.items.forEach((artist, index) => addArtist(artistMap, artist, 'medium_term_rank', index + 1));
  longTerm.items.forEach((artist, index) => addArtist(artistMap, artist, 'long_term_rank', index + 1));

  const recentMap = {};
  for (const item of recent.items || []) {
    const artist = item.track?.artists?.[0];
    if (!artist) continue;
    const playedAt = new Date(item.played_at);
    if (!recentMap[artist.id]) {
      recentMap[artist.id] = { last_played_at: playedAt, play_count: 0 };
    }
    if (playedAt > recentMap[artist.id].last_played_at) {
      recentMap[artist.id].last_played_at = playedAt;
    }
    recentMap[artist.id].play_count++;
  }

  const now = new Date();
  for (const [id, data] of Object.entries(recentMap)) {
    if (!artistMap[id]) continue;
    artistMap[id].last_played_at = data.last_played_at.toISOString();
    artistMap[id].days_since_played = Math.floor((now - data.last_played_at) / (1000 * 60 * 60 * 24));
    artistMap[id].recent_play_count = data.play_count;
  }

  return Object.values(artistMap).sort((a, b) => {
    const aRank = a.short_term_rank ?? a.medium_term_rank ?? a.long_term_rank ?? 999;
    const bRank = b.short_term_rank ?? b.medium_term_rank ?? b.long_term_rank ?? 999;
    return aRank - bRank;
  });
}

function toTmDate(date) {
  return date.toISOString().split('.')[0] + 'Z';
}

function ticketmasterSearchWindows() {
  const now = new Date();
  const end = new Date(now);
  end.setMonth(end.getMonth() + MONTHS_AHEAD);

  const windows = [];
  let cursor = new Date(now);
  while (cursor < end) {
    const windowStart = new Date(cursor);
    const windowEnd = new Date(cursor);
    windowEnd.setMonth(windowEnd.getMonth() + 1);
    if (windowEnd > end) windowEnd.setTime(end.getTime());

    windows.push({
      startDateTime: toTmDate(windowStart),
      endDateTime: toTmDate(windowEnd),
    });

    cursor = windowEnd;
  }

  return windows;
}

async function fetchTicketmasterPage(window) {
  const params = new URLSearchParams({
    apikey: TICKETMASTER_API_KEY,
    latlong: `${USER_LAT},${USER_LNG}`,
    radius: RADIUS_MILES,
    unit: 'miles',
    classificationName: 'music',
    size: '200',
    sort: 'date,asc',
    startDateTime: window.startDateTime,
    endDateTime: window.endDateTime,
    page: String(window.page),
  });

  return fetchJson(`https://app.ticketmaster.com/discovery/v2/events.json?${params}`);
}

async function fetchTicketmasterResponse() {
  const windows = ticketmasterSearchWindows();
  const pages = [];
  let maxWindowTotalPages = 0;

  for (const window of windows) {
    const firstPage = await fetchTicketmasterPage({ ...window, page: 0 });
    pages.push(firstPage);

    const totalPages = firstPage.page?.totalPages || 1;
    maxWindowTotalPages = Math.max(maxWindowTotalPages, totalPages);
    const pagesToFetch = Math.min(totalPages, MAX_PAGES_PER_WINDOW);

    for (let page = 1; page < pagesToFetch; page++) {
      pages.push(await fetchTicketmasterPage({ ...window, page }));
    }
  }

  const seen = new Set();
  const events = [];
  for (const response of pages) {
    for (const event of response._embedded?.events || []) {
      if (seen.has(event.id)) continue;
      seen.add(event.id);
      events.push(event);
    }
  }

  return {
    ...pages[0],
    _embedded: {
      ...(pages[0]?._embedded || {}),
      events,
    },
    qa: {
      fetchedPages: pages.length,
      totalPages: maxWindowTotalPages,
      monthsAhead: MONTHS_AHEAD,
      maxPagesPerWindow: MAX_PAGES_PER_WINDOW,
    },
  };
}


async function main() {
  requireEnv('SPOTIFY_ACCESS_TOKEN', SPOTIFY_ACCESS_TOKEN);
  requireEnv('TICKETMASTER_API_KEY', TICKETMASTER_API_KEY);
  if (process.exitCode) return;

  fs.mkdirSync(OUT_DIR, { recursive: true });

  console.log('Fetching Spotify artist profile...');
  const artists = await buildArtistProfile();
  fs.writeFileSync(ARTISTS_OUT, JSON.stringify(artists, null, 2) + '\n');
  console.log(`Wrote ${artists.length} artists to ${ARTISTS_OUT}`);

  console.log('Fetching Ticketmaster events...');
  const ticketmaster = await fetchTicketmasterResponse();
  const eventCount = ticketmaster._embedded?.events?.length || 0;
  fs.writeFileSync(TICKETMASTER_OUT, JSON.stringify(ticketmaster, null, 2) + '\n');
  console.log(`Wrote ${eventCount} Ticketmaster events from ${ticketmaster.qa.fetchedPages} monthly-window pages to ${TICKETMASTER_OUT}`);
  console.log(`Ticketmaster reported up to ${ticketmaster.qa.totalPages} pages in a window. Max pages per window is ${ticketmaster.qa.maxPagesPerWindow}.`);
}

main().catch(error => {
  console.error(error.message);
  process.exit(1);
});
