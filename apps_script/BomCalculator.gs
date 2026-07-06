// ═══════════════════════════════════════════════════════════════════════════
// BomCalculator.gs  —  Layer-by-layer BOM calculation (pure JS, no Sheets I/O)
//
// Main entry: buildBomForCable(cable, bomType, calcMode)
//   cable    : parsed GTP cable object from Claude extraction
//   bomType  : 'A' | 'B' | 'C'   — affects CR factor, armour coverage/thickness
//   calcMode : 'costing' | 'production'  — affects tolerance, lay factors, density
//
// Returns: array of {layer, material, weight_kg_per_km, od_after_mm}
// ═══════════════════════════════════════════════════════════════════════════


// ── Embedded data tables ──────────────────────────────────────────────────

var IS10462 = [
  {area:1.5,  od:1.4},  {area:2.5,  od:1.8},  {area:4,    od:2.3},
  {area:6,    od:2.8},  {area:10,   od:3.6},   {area:16,   od:4.5},
  {area:25,   od:5.6},  {area:35,   od:6.7},   {area:50,   od:8.0},
  {area:70,   od:9.4},  {area:95,   od:11.0},  {area:120,  od:12.4},
  {area:150,  od:13.8}, {area:185,  od:15.3},  {area:240,  od:17.5},
  {area:300,  od:19.5}, {area:400,  od:22.6},  {area:500,  od:25.2},
  {area:630,  od:28.5}, {area:800,  od:31.9},  {area:1000, od:35.7},
];

// Shattuc concentric lay-up M factors (n cores → M)
var SHATTUC_CONCENTRIC = {
  1:1.00, 2:2.00, 3:2.15, 4:2.41, 5:2.70, 6:3.00, 7:3.00,
  8:3.31, 9:3.62, 10:4.00, 12:4.15, 19:5.00, 37:7.00, 61:9.00,
};

// Shattuc twisted pair M factors (n pairs → M)
var SHATTUC_TWISTED_PAIR = {
  1:2.0, 2:3.5, 3:3.8, 4:4.2, 5:4.8, 6:5.0, 7:5.0, 8:5.6,
  9:6.0, 10:6.5, 12:6.8, 19:8.0, 24:9.5, 37:11.0,
};

// Densities (g/cm³)
var DENSITY = {
  copper_conductor:     8.89,
  aluminium_conductor:  2.703,
  xlpe_insulation:      0.92,
  pvc_insulation:       1.50,   // unarmoured flexible
  pvc_armoured_sheath:  1.60,
  pvc_outer_sheath:     1.60,
  pvc_inner_sheath:     1.60,
  pvc_frlsh_sheath:     1.60,
  bedding:              1.60,
  frlsh_sheath:         1.50,
  hffr_sheath:          1.50,
  lszh_sheath:          1.50,
  lszh_outer_sheath:    1.50,
  frlsh_outer_sheath:   1.50,
  rubber_insulation:    1.35,
  rubber_epdm:          1.35,
  semicon_screen:       1.20,
  conductor_screen:     1.20,
  insulation_screen:    1.20,
  gs_flat_strip_armour: 7.85,
  gs_round_wire_armour: 7.85,
  copper_tape_screen:   8.89,
  copper_wire_screen:   8.89,
  binder_tape:          1.35,
  binding_tape_pp:      0.91,
  petp_tape:            1.39,
  swelling_tape:        1.00,
  glass_mica_tape:      1.40,
  pe_tape:              1.50,
  al_mylar_pe_tape:     1.50,
  pp_filler:            0.91,
  pvc_filler:           1.70,
  filler_compound:      1.40,
};

// CR factor (conductor resistance factor) by BOM type
var CR_FACTOR = { A: 1.00, B: 0.92, C: 0.90 };

// Armour coverage fraction by BOM type
var ARMOUR_COVERAGE = { A: 0.90, B: 0.80, C: 0.80 };

// Lay factors
function getCablingLayFactor(calcMode) {
  return calcMode === 'costing' ? 1.008 : 1.007;
}

function getConductorLayFactor(nWires, calcMode, isFinewire) {
  if (nWires <= 1) return 1.000;  // compacted / solid — forced
  if (isFinewire) {
    if (nWires <= 130) return calcMode === 'costing' ? 1.030 : 1.025;
    return calcMode === 'costing' ? 1.040 : 1.035;
  }
  if (nWires <= 7)  return 1.005;
  if (nWires <= 19) return 1.010;
  if (nWires <= 37) return 1.020;
  return 1.025;
}

function getArmourLayFactor(calcMode) {
  return calcMode === 'costing' ? 1.008 : 1.007;
}


// ── Lookup helpers ────────────────────────────────────────────────────────

function getIS10462Od(areaMm2) {
  for (var i = 0; i < IS10462.length; i++) {
    if (IS10462[i].area === areaMm2) return IS10462[i].od;
  }
  // Nearest match
  var best = IS10462[0], bestDiff = Math.abs(IS10462[0].area - areaMm2);
  for (var j = 1; j < IS10462.length; j++) {
    var diff = Math.abs(IS10462[j].area - areaMm2);
    if (diff < bestDiff) { best = IS10462[j]; bestDiff = diff; }
  }
  return best.od;
}

function getShattucFactor(n, tableType) {
  var table = tableType === 'twisted_pair' ? SHATTUC_TWISTED_PAIR : SHATTUC_CONCENTRIC;
  if (table[n] !== undefined) return table[n];
  // Interpolate between nearest entries
  var keys = Object.keys(table).map(Number).sort(function(a,b){return a-b;});
  for (var i = 0; i < keys.length - 1; i++) {
    if (n >= keys[i] && n <= keys[i+1]) {
      var t = (n - keys[i]) / (keys[i+1] - keys[i]);
      return table[keys[i]] + t * (table[keys[i+1]] - table[keys[i]]);
    }
  }
  return table[keys[keys.length - 1]]; // beyond table — use last
}

function getDensity(materialKey) {
  return DENSITY[materialKey] || 1.0;
}

function r(val, dp) {
  dp = dp !== undefined ? dp : 3;
  var factor = Math.pow(10, dp);
  return Math.round(val * factor) / factor;
}


// ═══════════════════════════════════════════════════════════════════════════
// CONDUCTOR  (Layer 1)
// ═══════════════════════════════════════════════════════════════════════════

function calcConductorOd(areaMm2, shape, conductorType, nWires) {
  if (shape === 'sector') return null;  // sector: caller uses 2r from IS 10462

  var baseOd = getIS10462Od(areaMm2);

  if (conductorType === 'compacted') {
    return r(baseOd + 0.2);  // IS 10462 + 0.2mm
  }
  if (conductorType === 'flexible') {
    var wireDia = Math.sqrt((4 * areaMm2) / (Math.PI * nWires));
    return r(Math.sqrt(nWires) * wireDia * 1.13);
  }
  // Round stranded — wire count determines multiplier
  var multipliers = {1:1, 7:3, 19:5, 37:7, 61:9};
  var mult = multipliers[nWires];
  if (!mult) {
    // Fallback: approximate
    var wireDia2 = Math.sqrt((4 * areaMm2) / (Math.PI * nWires));
    return r(baseOd + 0.2);
  }
  var wireDia3 = Math.sqrt((4 * areaMm2) / (Math.PI * nWires));
  return r(mult * wireDia3);
}

function calcSectorR(areaMm2) {
  // r = IS 10462 fictitious dia / 2  (used as 2r = ID for insulation)
  return r(getIS10462Od(areaMm2) / 2);
}

function calcConductorWeight(areaMm2, conductorMaterial, conductorType,
                              nWires, isFinewire, rDcOhmPerKm, bomType, calcMode, numCores) {
  var crFactor  = CR_FACTOR[bomType] || 1.0;
  var density   = getDensity(conductorMaterial === 'aluminium' ? 'aluminium_conductor' : 'copper_conductor');
  var resistivity = conductorMaterial === 'aluminium' ? (1/35) : (1/58);
  var layFactor = getConductorLayFactor(nWires || 7, calcMode, isFinewire);

  // If compacted, lay factor forced to 1.0
  if (conductorType === 'compacted') layFactor = 1.0;

  var areaMm2Eff;
  if (rDcOhmPerKm && rDcOhmPerKm > 0) {
    var rDcPerM = rDcOhmPerKm / 1000;
    areaMm2Eff  = (resistivity / rDcPerM) * crFactor;
  } else {
    areaMm2Eff = areaMm2 * crFactor;
  }

  var weightKgPerKm = areaMm2Eff * density * layFactor * (numCores || 1);
  return { weight_kg_per_km: r(weightKgPerKm), effective_area_mm2: r(areaMm2Eff) };
}


// ═══════════════════════════════════════════════════════════════════════════
// ANNULAR LAYER  (insulation, screens, sheaths, bedding)
// ═══════════════════════════════════════════════════════════════════════════

function calcAnnularWeight(innerOdMm, thicknessMm, materialKey,
                            applyLayFactor, layFactor, numCores) {
  var density      = getDensity(materialKey);
  var outerOdMm    = innerOdMm + 2 * thicknessMm;
  var areaMm2      = (Math.PI / 4) * (outerOdMm * outerOdMm - innerOdMm * innerOdMm);
  var lf           = applyLayFactor ? (layFactor || 1.0) : 1.0;
  var weight       = areaMm2 * density * lf * (numCores || 1);
  return r(weight);
}

function calcSectorInsulationWeight(sectorR, thicknessMm, thetaDeg, materialKey,
                                     cablingLayFactor, numCores) {
  var density  = getDensity(materialKey);
  var r2       = sectorR + 2 * thicknessMm;
  var areaMm2  = (thetaDeg / 360) * Math.PI * (r2 * r2 - sectorR * sectorR);
  var weight   = areaMm2 * density * cablingLayFactor * (numCores || 1);
  return r(weight);
}


// ═══════════════════════════════════════════════════════════════════════════
// EFFECTIVE THICKNESS  (tolerance-adjusted)
// ═══════════════════════════════════════════════════════════════════════════

function effectiveThickness(nominalMm, thicknessType, calcMode) {
  // Tolerance factor table (mirrors Config/Extrusion_Tolerances)
  var table = [
    {type:'Nominal', min:0,   max:1.0, costing:1.10, production:1.00},
    {type:'Nominal', min:1.0, max:2.0, costing:1.08, production:1.00},
    {type:'Nominal', min:2.0, max:99,  costing:1.05, production:1.00},
    {type:'Minimum', min:0,   max:1.0, costing:1.15, production:1.05},
    {type:'Minimum', min:1.0, max:2.0, costing:1.12, production:1.05},
    {type:'Minimum', min:2.0, max:99,  costing:1.08, production:1.05},
  ];
  for (var i = 0; i < table.length; i++) {
    var row = table[i];
    if (row.type === thicknessType && nominalMm >= row.min && nominalMm < row.max) {
      return nominalMm * (calcMode === 'costing' ? row.costing : row.production);
    }
  }
  return nominalMm;
}


// ═══════════════════════════════════════════════════════════════════════════
// TAPE WRAP  (generic — binder tape, swelling tape, PETP, PE, Al Mylar)
// ═══════════════════════════════════════════════════════════════════════════

function calcTapeWrapWeight(innerOdMm, thicknessMm, overlapPct, materialKey, nLayers) {
  var density  = getDensity(materialKey);
  var meanOd   = innerOdMm + thicknessMm;
  var areaMm2  = Math.PI * meanOd * (1 + overlapPct / 100) * thicknessMm;
  return r(areaMm2 * density * (nLayers || 1));
}


// ═══════════════════════════════════════════════════════════════════════════
// GLASS MICA TAPE  (confirmed: 0.12/0.11mm, 30%/25%, n_tapes=2, per core)
// ═══════════════════════════════════════════════════════════════════════════

function calcGlassMicaTapeWeight(innerOdMm, calcMode, nTapes, numCores) {
  var thickness = calcMode === 'costing' ? 0.12 : 0.11;
  var overlap   = calcMode === 'costing' ? 30.0 : 25.0;
  var density   = getDensity('glass_mica_tape');
  var meanOd    = innerOdMm + thickness;
  var areaMm2   = Math.PI * meanOd * (1 + overlap / 100) * thickness;
  return r(areaMm2 * density * (nTapes || 2) * (numCores || 1));
}


// ═══════════════════════════════════════════════════════════════════════════
// COPPER TAPE SCREEN
// ═══════════════════════════════════════════════════════════════════════════

function calcCopperTapeScreenWeight(innerOdMm, thicknessMm, overlapPct, numCores) {
  var density  = getDensity('copper_tape_screen');
  var meanOd   = innerOdMm + thicknessMm;
  var areaMm2  = Math.PI * meanOd * (1 + overlapPct / 100) * thicknessMm;
  return r(areaMm2 * density * (numCores || 1));
}


// ═══════════════════════════════════════════════════════════════════════════
// ARMOUR
// ═══════════════════════════════════════════════════════════════════════════

function calcFlatStripArmourWeight(cableOdMm, stripWidthMm, stripThicknessMm,
                                    bomType, calcMode) {
  var coverage  = ARMOUR_COVERAGE[bomType] || 0.9;
  var density   = getDensity('gs_flat_strip_armour');
  var D         = cableOdMm;
  // Simplified formula: lay factor (cosα) cancels
  var weight    = coverage * Math.PI * D * stripThicknessMm * density * getArmourLayFactor(calcMode);
  return r(weight);
}

function calcRoundWireArmourWeight(cableOdMm, wireDiaMm, bomType, calcMode) {
  var coverage  = ARMOUR_COVERAGE[bomType] || 0.9;
  var density   = getDensity('gs_round_wire_armour');
  var wireArea  = (Math.PI / 4) * wireDiaMm * wireDiaMm;
  var weight    = coverage * Math.PI * cableOdMm * wireArea * density * getArmourLayFactor(calcMode);
  return r(weight);
}


// ═══════════════════════════════════════════════════════════════════════════
// PP FILLER  (circular cables, fill factor method)
// ═══════════════════════════════════════════════════════════════════════════

function calcPpFillerWeight(odCabledMm, odCoreMm, numCores, fillFactor) {
  var density      = getDensity('pp_filler');
  fillFactor       = fillFactor || 0.87;
  var cabledArea   = (Math.PI / 4) * odCabledMm * odCabledMm;
  var coreArea     = (Math.PI / 4) * odCoreMm * odCoreMm * numCores;
  var fillerArea   = Math.max(0, cabledArea - coreArea) * fillFactor;
  return r(fillerArea * density);
}


// ═══════════════════════════════════════════════════════════════════════════
// CABLE ASSEMBLY OD
// ═══════════════════════════════════════════════════════════════════════════

function calcAssemblyOd(coreOdMm, numCores, isSector, isTwistedPair, bomType) {
  var factor;
  if (isSector) {
    factor = bomType === 'costing' ? 1.05 : 1.03;
    return r(2 * coreOdMm * factor);
  }
  if (isTwistedPair) {
    factor = getShattucFactor(numCores, 'twisted_pair');
  } else {
    factor = getShattucFactor(numCores, 'concentric');
  }
  return r(factor * coreOdMm);
}


// ═══════════════════════════════════════════════════════════════════════════
// MAIN BOM BUILDER
// ═══════════════════════════════════════════════════════════════════════════

/**
 * @param {Object} cable      - GTP cable object from Claude extraction
 * @param {string} bomType    - 'A' | 'B' | 'C'
 * @param {string} calcMode   - 'costing' | 'production'
 * @returns {Array}           - [{layer, material, weight_kg_per_km, od_after_mm}]
 */
function buildBomForCable(cable, bomType, calcMode) {
  var bom          = [];
  var layers       = cable.layers || [];
  var numCores     = cable.num_cores || 1;
  var isSector     = cable.conductor_shape === 'sector';
  var isTwistedPair = (cable.cable_type || '').toLowerCase().indexOf('pair') !== -1;
  var isFlex       = cable.conductor_type === 'flexible';
  var isFinewire   = isFlex;
  var cablingLF    = getCablingLayFactor(calcMode);

  // Track OD as we build up layers
  var currentOd    = 0;
  var sectorR      = 0;   // for sector conductors: track per-core radius
  var perCoreKeys  = {
    conductor:true, fine_wire_conductor:true,
    conductor_screen:true, insulation_screen:true,
    xlpe_insulation:true, pvc_insulation:true, rubber_insulation:true,
    glass_mica_tape:true, copper_tape_screen:true,
    pe_tape:true, al_mylar_pe_tape:true,
  };
  var assemblyOdSet = false;

  for (var i = 0; i < layers.length; i++) {
    var layer    = layers[i];
    var matKey   = layer.material_key || '';
    var layerName = layer.layer_name || matKey;
    var isPerCore = perCoreKeys[matKey] === true;

    // At first outer-cable layer for sector: compute cabled OD
    if (isSector && !assemblyOdSet && !isPerCore) {
      var laidupOd = calcAssemblyOd(currentOd, numCores, true, false, bomType);
      currentOd    = laidupOd;
      assemblyOdSet = true;
    }

    var weight = 0;

    // ── CONDUCTOR ──────────────────────────────────────────────────────────
    if (matKey === 'conductor' || matKey === 'fine_wire_conductor') {
      var area    = cable.conductor_area_mm2 || 0;
      var nWires  = layer.n_wires || estimateNWires(area, cable.conductor_type);
      var condResult = calcConductorWeight(
        area, cable.conductor_material || 'copper',
        cable.conductor_type || 'stranded', nWires, isFinewire,
        cable.conductor_resistance_ohm_per_km, bomType, calcMode, numCores
      );
      weight    = condResult.weight_kg_per_km;
      var effArea = condResult.effective_area_mm2;

      if (isSector) {
        sectorR  = calcSectorR(area);
        currentOd = sectorR * 2;   // 2r = ID for insulation
      } else {
        currentOd = calcConductorOd(
          effArea, cable.conductor_shape || 'circular',
          cable.conductor_type || 'stranded', nWires
        );
      }

    // ── INSULATION ─────────────────────────────────────────────────────────
    } else if (matKey === 'xlpe_insulation' || matKey === 'pvc_insulation' ||
               matKey === 'rubber_insulation') {
      var nomT = layer.nominal_thickness_mm || layer.minimum_thickness_mm || 0;
      var tType = layer.thickness_type || 'Nominal';
      var effT  = effectiveThickness(nomT, tType, calcMode);
      var density_ins = getDensity(matKey);

      // PVC sheath density (1.60) applies only when armoured; insulation-level PVC uses 1.50
      // The getDensity('pvc_insulation') is 1.50 — correct for insulation layer
      if (isSector) {
        // θ = 360/numCores (simplified — 3C: 120°, 4C: 90°)
        var theta = 360 / (numCores > 3 ? 3 : numCores);  // 3.5C: use 3 phase cores
        weight    = calcSectorInsulationWeight(sectorR, effT, theta, matKey, cablingLF, numCores);
        sectorR   = sectorR + 2 * effT;
        currentOd = sectorR * 2;
      } else {
        weight    = calcAnnularWeight(currentOd, effT, matKey, true, cablingLF, numCores);
        currentOd = currentOd + 2 * effT;
      }

    // ── CONDUCTOR / INSULATION SCREEN ─────────────────────────────────────
    } else if (matKey === 'conductor_screen' || matKey === 'insulation_screen') {
      if (isSector) {
        // Screens don't exist on sector conductors
        continue;
      }
      var scrT  = layer.nominal_thickness_mm || 0.5;
      var effScrT = effectiveThickness(scrT, layer.thickness_type || 'Nominal', calcMode);
      weight    = calcAnnularWeight(currentOd, effScrT, matKey, true, cablingLF, numCores);
      currentOd = currentOd + 2 * effScrT;

    // ── COPPER TAPE SCREEN ─────────────────────────────────────────────────
    } else if (matKey === 'copper_tape_screen') {
      var ctT   = layer.tape_thickness_mm || 0.10;
      var ctOvl = layer.tape_overlap_pct !== undefined ? layer.tape_overlap_pct : 12.0;
      weight    = calcCopperTapeScreenWeight(currentOd, ctT, ctOvl, numCores);
      currentOd = currentOd + 2 * ctT;

    // ── GLASS MICA TAPE ────────────────────────────────────────────────────
    } else if (matKey === 'glass_mica_tape') {
      var gmTapes = layer.n_tapes || 2;
      var gmT     = calcMode === 'costing' ? 0.12 : 0.11;
      weight      = calcGlassMicaTapeWeight(currentOd, calcMode, gmTapes, numCores);
      currentOd   = currentOd + 2 * gmTapes * gmT;

    // ── PE TAPE / AL MYLAR PE TAPE ─────────────────────────────────────────
    } else if (matKey === 'pe_tape' || matKey === 'al_mylar_pe_tape') {
      var ptT   = layer.tape_thickness_mm || 0.04;
      var ptOvl = layer.tape_overlap_pct !== undefined ? layer.tape_overlap_pct : 15.0;
      var nPairs = layer.n_pairs || 1;
      weight    = calcTapeWrapWeight(currentOd, ptT, ptOvl, matKey, nPairs);
      currentOd = currentOd + 2 * ptT;

    // ── DRAIN WIRE  (fixed weight; bumps OD before screens) ───────────────
    } else if (matKey === 'drain_wire') {
      var nPairsDw = layer.n_pairs || 1;
      var dwWeightPerPair = calcMode === 'costing' ? 4.5 : 3.0;
      weight      = dwWeightPerPair * nPairsDw;
      currentOd   = currentOd + 0.5;  // bump OD to account for drain wire

    // ── GENERIC TAPE WRAP (binder, swelling, PP binding, PETP) ────────────
    } else if (matKey === 'binder_tape' || matKey === 'swelling_tape' ||
               matKey === 'binding_tape_pp' || matKey === 'petp_tape') {
      var gtT   = layer.tape_thickness_mm || 0.15;
      var gtOvl = layer.tape_overlap_pct !== undefined ? layer.tape_overlap_pct : 12.0;
      weight    = calcTapeWrapWeight(currentOd, gtT, gtOvl, matKey, 1);
      currentOd = currentOd + 2 * gtT;

    // ── CABLE ASSEMBLY  (implicit — triggered by first outer-cable layer) ──
    } else if (matKey === 'pp_filler') {
      if (!assemblyOdSet && !isSector) {
        var asmOd  = calcAssemblyOd(currentOd, numCores, false, isTwistedPair, bomType);
        assemblyOdSet = true;
        var ppFill = calcPpFillerWeight(asmOd, currentOd, numCores, 0.87);
        bom.push({ layer: 'PP Filler', material: 'pp_filler',
                   weight_kg_per_km: ppFill, od_after_mm: asmOd });
        currentOd = asmOd;
        continue;
      }
      // If assembly already set, still calc filler for remaining void
      var ppFill2 = calcPpFillerWeight(currentOd, currentOd * 0.87, numCores, 0.87);
      weight = ppFill2;

    // ── BEDDING / INNER SHEATH ─────────────────────────────────────────────
    } else if (matKey === 'bedding' || matKey === 'pvc_inner_sheath') {
      if (!assemblyOdSet && !isSector) {
        var asmOdB = calcAssemblyOd(currentOd, numCores, false, isTwistedPair, bomType);
        currentOd  = asmOdB;
        assemblyOdSet = true;
      }
      var bdT = layer.nominal_thickness_mm || layer.minimum_thickness_mm || 0;
      var bdEffT = effectiveThickness(bdT, layer.thickness_type || 'Nominal', calcMode);
      weight    = calcAnnularWeight(currentOd, bdEffT, matKey, false, 1.0, 1);
      currentOd = currentOd + 2 * bdEffT;

    // ── ARMOUR ─────────────────────────────────────────────────────────────
    } else if (matKey === 'gs_flat_strip_armour') {
      if (!assemblyOdSet && !isSector) {
        currentOd = calcAssemblyOd(currentOd, numCores, false, isTwistedPair, bomType);
        assemblyOdSet = true;
      }
      var sw = layer.armour_strip_width_mm     || 0;
      var st = layer.armour_strip_thickness_mm || 0;
      if (sw && st) {
        weight    = calcFlatStripArmourWeight(currentOd, sw, st, bomType, calcMode);
        currentOd = currentOd + 2 * st;
      }

    } else if (matKey === 'gs_round_wire_armour') {
      if (!assemblyOdSet && !isSector) {
        currentOd = calcAssemblyOd(currentOd, numCores, false, isTwistedPair, bomType);
        assemblyOdSet = true;
      }
      var wd = layer.wire_diameter_mm || 1.6;
      weight    = calcRoundWireArmourWeight(currentOd, wd, bomType, calcMode);
      currentOd = currentOd + 2 * wd;

    // ── OUTER SHEATH ───────────────────────────────────────────────────────
    } else if (matKey.indexOf('sheath') !== -1 ||
               matKey === 'frlsh_outer_sheath' || matKey === 'lszh_outer_sheath') {
      if (!assemblyOdSet && !isSector) {
        currentOd = calcAssemblyOd(currentOd, numCores, false, isTwistedPair, bomType);
        assemblyOdSet = true;
      }
      var shT = layer.nominal_thickness_mm || layer.minimum_thickness_mm || 0;
      var shEffT = effectiveThickness(shT, layer.thickness_type || 'Nominal', calcMode);
      weight    = calcAnnularWeight(currentOd, shEffT, matKey, false, 1.0, 1);
      currentOd = currentOd + 2 * shEffT;

    } else {
      // Unknown layer — log and skip
      Logger.log('BomCalculator: unknown material_key: ' + matKey);
      continue;
    }

    bom.push({
      layer:             layerName,
      material:          matKey,
      weight_kg_per_km:  r(weight),
      od_after_mm:       r(currentOd),
    });
  }

  return bom;
}


// ═══════════════════════════════════════════════════════════════════════════
// UTILITY
// ═══════════════════════════════════════════════════════════════════════════

function estimateNWires(areaMm2, conductorType) {
  if (conductorType === 'compacted') return 1;
  if (conductorType === 'flexible')  return Math.round(areaMm2 / 0.03);  // ~0.03mm² per wire
  // Standard round stranded — common wire counts from IS
  if (areaMm2 <= 6)   return 7;
  if (areaMm2 <= 16)  return 7;
  if (areaMm2 <= 50)  return 19;
  if (areaMm2 <= 150) return 37;
  if (areaMm2 <= 300) return 61;
  return 61;
}
