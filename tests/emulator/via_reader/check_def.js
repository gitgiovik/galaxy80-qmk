// usage: node check_def.js <galaxy80.json>  -> prints a JSON verdict (VIA's own reader package)
const r = require('@the-via/reader');
const fs = require('fs');
const def = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = {valid: false};
try {
  out.valid = r.isKeyboardDefinitionV3(def);
  if (!out.valid) out.errors = (r.isKeyboardDefinitionV3.errors || []).slice(0, 5);
  const v = r.keyboardDefinitionV3ToVIADefinitionV3(def);
  out.keys = v.layouts.keys.map(k => [k.row, k.col, k.li === undefined ? null : k.li]);
  out.optionKeys = [];
  for (const g of Object.values(v.layouts.optionKeys || {}))
    for (const opt of Object.values(g)) for (const k of opt) out.optionKeys.push([k.row, k.col, k.li === undefined ? null : k.li]);
  out.ok = true;
} catch (e) { out.exception = String(e); }
console.log(JSON.stringify(out));
