// Emits resolved champions-mod dex data as JSON on stdout.
// Run from the pokemon-showdown checkout root.
//
// Showdown mods are deltas over the base dex, so we let Showdown resolve the
// overlay rather than reimplementing inheritance in Python.
const {Dex} = require('./dist/sim/dex');
const dex = Dex.mod('champions');

const isStandard = (e) => e.exists && !e.isNonstandard;

// getLearnsetData returns only a species' OWN learnset. Moves inherited from
// pre-evolutions must be merged in or the validator rejects legal sets
// (Garchomp: 58 own moves, 92 with the prevo chain).
function fullLearnset(id) {
  const out = new Set();
  const seen = new Set();
  let cur = dex.species.get(id);
  while (cur && cur.exists && !seen.has(cur.id)) {
    seen.add(cur.id);
    for (const ls of (dex.species.getFullLearnset(cur.id) || [])) {
      if (ls && ls.learnset) for (const m of Object.keys(ls.learnset)) out.add(m);
    }
    cur = cur.prevo ? dex.species.get(cur.prevo) : null;
  }
  return [...out];
}

const species = {};
const learnsets = {};
for (const s of dex.species.all()) {
  if (!isStandard(s)) continue;
  species[s.id] = {
    name: s.name,
    baseSpecies: s.baseSpecies,
    forme: s.forme,
    types: s.types,
    baseStats: s.baseStats,
    abilities: s.abilities,
    isMega: !!s.isMega,
    requiredItem: s.requiredItem || null,
    prevo: s.prevo || null,
    weightkg: s.weightkg,
    heightm: s.heightm,
  };
  const ls = fullLearnset(s.id);
  if (ls.length) learnsets[s.id] = ls;
}

const moves = {};
for (const m of dex.moves.all()) {
  if (!isStandard(m)) continue;
  moves[m.id] = {
    name: m.name,
    type: m.type,
    category: m.category,
    basePower: m.basePower,
    accuracy: m.accuracy,
    priority: m.priority,
    target: m.target,
    pp: m.pp,
  };
}

const items = {};
for (const i of dex.items.all()) {
  if (!isStandard(i)) continue;
  items[i.id] = {name: i.name, megaStone: i.megaStone || null};
}

const abilities = {};
for (const a of dex.abilities.all()) {
  if (!isStandard(a)) continue;
  abilities[a.id] = {name: a.name};
}

const natures = {};
for (const n of dex.natures.all()) {
  if (!n.exists) continue;
  natures[n.id] = {name: n.name, plus: n.plus || null, minus: n.minus || null};
}

process.stdout.write(JSON.stringify({species, moves, learnsets, items, abilities, natures}));
