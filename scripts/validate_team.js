// Validates a Showdown team paste on stdin against Reg M-B.
// Prints "LEGAL" or one problem per line. Run from the pokemon-showdown root.
const {TeamValidator} = require('./dist/sim/team-validator');
const {Teams} = require('./dist/sim/teams');

const FORMAT = 'gen9championsvgc2026regmb';

let input = '';
process.stdin.on('data', (c) => { input += c; });
process.stdin.on('end', () => {
  let team;
  try {
    team = Teams.import(input);
  } catch (e) {
    console.log('IMPORT ERROR: ' + e.message);
    return;
  }
  const problems = new TeamValidator(FORMAT).validateTeam(team);
  console.log(problems ? problems.join('\n') : 'LEGAL');
});
