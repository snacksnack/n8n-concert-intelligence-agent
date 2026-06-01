#!/usr/bin/env node
/*
  QA Ticketmaster matching outside n8n.

  Usage:
    node scripts/qa-ticketmaster-matching.js --artists artists.json --ticketmaster ticketmaster.json --expect Lorde --expect "Blood Orange"
    node scripts/qa-ticketmaster-matching.js --artists artists.json --ticketmaster ticketmaster.json --festival-hints qa/festival-lineup-hints.example.json --expect Lorde

  artists.json can be either:
    [{ "name": "Lorde", ... }]
  or n8n-style:
    [{ "json": { "name": "Lorde", ... } }]
*/
const fs = require('fs');

function artistPriority(artist) {
  const ranks = [artist.short_term_rank, artist.medium_term_rank, artist.long_term_rank]
    .filter(v => v != null)
    .map(Number);
  const bestRank = ranks.length ? Math.min(...ranks) : 999;
  const days = artist.days_since_played == null ? 999 : Number(artist.days_since_played);
  const plays = artist.recent_play_count == null ? 0 : Number(artist.recent_play_count);
  return { bestRank, days, plays };
}

function compareArtistsForQa(a, b) {
  const pa = artistPriority(a);
  const pb = artistPriority(b);
  if (pa.days !== pb.days) return pa.days - pb.days;
  if (pa.bestRank !== pb.bestRank) return pa.bestRank - pb.bestRank;
  if (pa.plays !== pb.plays) return pb.plays - pa.plays;
  return String(a.name || '').localeCompare(String(b.name || ''));
}

function artistLabel(artist) {
  const p = artistPriority(artist);
  const bits = [];
  if (artist.short_term_rank != null) bits.push('short #' + artist.short_term_rank);
  if (artist.medium_term_rank != null) bits.push('medium #' + artist.medium_term_rank);
  if (artist.long_term_rank != null) bits.push('long #' + artist.long_term_rank);
  if (artist.days_since_played != null && Number(artist.days_since_played) < 999) bits.push(artist.days_since_played + 'd ago');
  if (artist.recent_play_count) bits.push(artist.recent_play_count + ' recent plays');
  if (!bits.length && p.bestRank === 999) bits.push('no rank metadata');
  return artist.name + (bits.length ? ' (' + bits.join(', ') + ')' : '');
}


function usage() {
  console.error('Usage: node scripts/qa-ticketmaster-matching.js --artists artists.json --ticketmaster ticketmaster.json [--festival-hints hints.json] [--include-event-text] [--expect Artist]');
  process.exit(2);
}

function parseArgs(argv) {
  const args = { expect: [] };
  for (let i = 2; i < argv.length; i++) {
    const key = argv[i];
    const value = argv[i + 1];
    if (key === '--artists') { args.artists = value; i++; continue; }
    if (key === '--ticketmaster') { args.ticketmaster = value; i++; continue; }
    if (key === '--expect') { args.expect.push(value); i++; continue; }
    if (key === '--festival-hints') { args.festivalHints = value; i++; continue; }
    if (key === '--include-event-text') { args.includeEventText = true; continue; }
    usage();
  }
  if (!args.artists || !args.ticketmaster) usage();
  return args;
}

function readJson(path) {
  return JSON.parse(fs.readFileSync(path, 'utf8'));
}

function unwrapItems(value) {
  if (!Array.isArray(value)) return [];
  return value.map(item => item && item.json ? item.json : item);
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

function textContainsArtist(text, artistName) {
  const normalizedText = ' ' + normalizeName(text) + ' ';
  const normalizedArtist = normalizeName(artistName);
  return normalizedArtist.length >= 3 && normalizedText.includes(' ' + normalizedArtist + ' ');
}

function levenshtein(a, b) {
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;
  const dp = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = 0; i <= a.length; i++) dp[i][0] = i;
  for (let j = 0; j <= b.length; j++) dp[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1]
        : 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
    }
  }
  return dp[a.length][b.length];
}

function makeMatcher(artists) {
  const artistByNormalized = {};
  for (const a of artists) {
    artistByNormalized[a.name_normalized || normalizeName(a.name)] = a;
  }

  function findMatch(eventArtistName) {
    const normalized = normalizeName(eventArtistName);
    if (artistByNormalized[normalized]) return artistByNormalized[normalized];
    for (const a of artists) {
      const artistNormalized = a.name_normalized || normalizeName(a.name);
      if (normalized.length < 8 || artistNormalized.length < 8) continue;
      const dist = levenshtein(normalized, artistNormalized);
      const maxLen = Math.max(normalized.length, artistNormalized.length);
      if (dist <= 1 && dist / maxLen <= 0.15) return a;
    }
    return null;
  }

  return findMatch;
}

function eventDate(ev) {
  return ev.dates?.start?.dateTime || ev.dates?.start?.localDate || null;
}

function eventDateKey(ev) {
  const date = eventDate(ev);
  return date ? String(date).slice(0, 10) : null;
}

function eventSearchText(ev) {
  const classifications = (ev.classifications || []).flatMap(c => [
    c.segment?.name,
    c.genre?.name,
    c.subGenre?.name,
    c.type?.name,
    c.subType?.name,
  ]);
  const promoters = [ev.promoter?.name, ...(ev.promoters || []).map(p => p.name)];
  const attractions = (ev._embedded?.attractions || []).map(a => a.name);
  return [
    ev.name,
    ev.info,
    ev.pleaseNote,
    ev.description,
    ...promoters,
    ...classifications,
    ...attractions,
  ].filter(Boolean).join(' | ');
}

function matchEvents(artists, ticketmasterResponse, options = {}) {
  const festivalLineupHints = options.festivalLineupHints || [];
  const includeEventText = Boolean(options.includeEventText);
  const events = ticketmasterResponse._embedded?.events || ticketmasterResponse.events || [];
  const findMatch = makeMatcher(artists);
  const matches = [];
  const seenConcertKeys = new Set();

  for (const ev of events) {
    const eventMatches = [];
    const seenArtistIds = new Set();
    const attractions = ev._embedded?.attractions || [];

    attractions.forEach((att, index) => {
      const artist = findMatch(att.name);
      if (!artist || seenArtistIds.has(artist.id || artist.name)) return;
      seenArtistIds.add(artist.id || artist.name);
      eventMatches.push({ artist, matchType: 'attraction', matchedName: att.name, isOpener: index > 0 });
    });

    const text = eventSearchText(ev);
    if (includeEventText) {
      for (const artist of artists) {
        if (seenArtistIds.has(artist.id || artist.name)) continue;
        if (!textContainsArtist(text, artist.name)) continue;
        seenArtistIds.add(artist.id || artist.name);
        eventMatches.push({ artist, matchType: 'event_text', matchedName: artist.name, isOpener: false });
      }
    }

    const normalizedText = normalizeName(text);
    const date = eventDateKey(ev);
    for (const hint of festivalLineupHints) {
      if (hint.date && hint.date !== date) continue;
      if (!hint.eventNameIncludes.some(fragment => normalizedText.includes(normalizeName(fragment)))) continue;
      for (const artistName of hint.artists) {
        const artist = findMatch(artistName);
        if (!artist || seenArtistIds.has(artist.id || artist.name)) continue;
        seenArtistIds.add(artist.id || artist.name);
        eventMatches.push({ artist, matchType: 'festival_hint', matchedName: artistName, isOpener: false });
      }
    }

    for (const match of eventMatches) {
      const concertKey = [
        match.artist.id || match.artist.name,
        eventDate(ev),
        normalizeName(ev._embedded?.venues?.[0]?.name),
      ].join('|');
      if (seenConcertKeys.has(concertKey)) continue;
      seenConcertKeys.add(concertKey);

      matches.push({
        artist: match.artist.name,
        matchedName: match.matchedName,
        matchType: match.matchType,
        event: ev.name,
        date: eventDate(ev),
        venue: ev._embedded?.venues?.[0]?.name || '',
        city: ev._embedded?.venues?.[0]?.city?.name || '',
        url: ev.url || '',
      });
    }
  }

  return matches;
}

const args = parseArgs(process.argv);
const artists = unwrapItems(readJson(args.artists));
const ticketmaster = readJson(args.ticketmaster);
const festivalLineupHints = args.festivalHints ? readJson(args.festivalHints) : [];
const matches = matchEvents(artists, ticketmaster, {
  festivalLineupHints,
  includeEventText: args.includeEventText,
});

const matchedNames = new Set(matches.map(m => normalizeName(m.artist)));
const missing = artists
  .filter(a => !matchedNames.has(normalizeName(a.name)))
  .sort(compareArtistsForQa);

const matchesByArtist = new Map();
for (const m of matches) {
  const key = normalizeName(m.artist);
  if (!matchesByArtist.has(key)) matchesByArtist.set(key, []);
  matchesByArtist.get(key).push(m);
}

console.log('Matched artists:');
const matchedArtists = artists
  .filter(a => matchedNames.has(normalizeName(a.name)))
  .sort(compareArtistsForQa);
for (const artist of matchedArtists) {
  const artistMatches = matchesByArtist.get(normalizeName(artist.name)) || [];
  console.log('- ' + artistLabel(artist));
  for (const m of artistMatches) {
    console.log('  -> ' + m.event + ' (' + (m.date || 'TBD') + ', ' + (m.venue || 'TBD') + ') [' + m.matchType + ']');
  }
}

console.log('\nArtists with no Ticketmaster match:');
for (const artist of missing) {
  console.log('- ' + artistLabel(artist));
}

console.log('\nSummary: ' + matches.length + ' event matches, ' + matchedArtists.length + ' matched artists, ' + missing.length + ' artists with no match.');

if (args.expect.length) {
  const failed = args.expect.filter(name => !matchedNames.has(normalizeName(name)));
  if (failed.length) {
    console.error('\nExpected artists not matched: ' + failed.join(', '));
    process.exit(1);
  }
  console.log('\nAll expected artists matched: ' + args.expect.join(', '));
}
