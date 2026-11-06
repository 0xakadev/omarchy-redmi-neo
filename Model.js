.pragma library

// Neutralizes HTML/StyledText markup characters (<, >, &) and control characters
// so untrusted strings (Bluetooth device aliases, error messages, codecs) never
// activate rich-text rendering in any UI component.
function neutralizeMarkup(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "")
    .replace(/&/g, "\uFF06")
    .replace(/</g, "\uFF1C")
    .replace(/>/g, "\uFF1E");
}

// Bar icon reflects connection state, Find-device alarm, and active ANC mode.
function barIcon(connected, anc, finding) {
  if (!connected) return "󰟎";               // nf-md-headphones_off
  if (finding) return "󰂞";                  // nf-md-bell_ring
  if (anc === "anc") return "󰥰";            // nf-md-headphones_bluetooth / ANC active
  if (anc === "transparency") return "󰟅";   // nf-md-ear_hearing / ambient
  return "󰋋";                               // nf-md-headphones
}

function batteryGlyph(pct, charging) {
  if (charging) return "󰂄";
  if (pct === null || pct === undefined || pct < 0) return "󰂑";
  if (pct >= 90) return "󰁹";
  if (pct >= 70) return "󰂀";
  if (pct >= 50) return "󰁾";
  if (pct >= 30) return "󰁼";
  if (pct >= 15) return "󰁺";
  return "󰂎";
}

function formatBattery(pct) {
  if (pct === null || pct === undefined || pct < 0 || pct > 100) return "—";
  return Math.round(Number(pct)) + "%";
}

function formatGainDb(val) {
  var n = Math.round(Number(val) || 0);
  if (n > 0) return "+" + n + " dB";
  return n + " dB";
}

function formatFreqLabel(hz) {
  var n = Number(hz) || 0;
  if (n >= 1000) {
    var k = Math.round(n / 1000);
    return k + "k";
  }
  return String(n);
}

function nextAnc(current) {
  var order = ["off", "anc", "transparency"];
  var idx = order.indexOf(current);
  if (idx < 0) return "anc";
  return order[(idx + 1) % order.length];
}

function ancOptions(tr) {
  var t = tr || function(k) { return k; };
  return [
    { value: "off",          label: t("anc.off") },
    { value: "anc",          label: t("anc.on") },
    { value: "transparency", label: t("anc.transparency") }
  ];
}

function ancLevelOptions(tr) {
  var t = tr || function(k) { return k; };
  return [
    { value: "light",    label: t("anc_level.light") },
    { value: "balanced", label: t("anc_level.balanced") },
    { value: "deep",     label: t("anc_level.deep") }
  ];
}

function transLevelOptions(tr) {
  var t = tr || function(k) { return k; };
  return [
    { value: "regular", label: t("trans_level.regular") },
    { value: "voice",   label: t("trans_level.voice") },
    { value: "ambient", label: t("trans_level.ambient") }
  ];
}

function eqOptions(tr) {
  var t = tr || function(k) { return k; };
  return [
    { value: "balanced", label: t("eq.balanced") },
    { value: "bass",     label: t("eq.bass") },
    { value: "treble",   label: t("eq.treble") },
    { value: "voice",    label: t("eq.voice") },
    { value: "custom",   label: t("eq.custom") }
  ];
}

function gestureSlots(tr) {
  var t = tr || function(k) { return k; };
  return [
    { key: "single", label: t("gesture.single") },
    { key: "double", label: t("gesture.double") },
    { key: "triple", label: t("gesture.triple") },
    { key: "long",   label: t("gesture.long") }
  ];
}

function gestureActionOptions(tr) {
  var t = tr || function(k) { return k; };
  return [
    { value: "play_pause",      label: t("action.play_pause") },
    { value: "next",            label: t("action.next") },
    { value: "previous",        label: t("action.previous") },
    { value: "volume_up",       label: t("action.volume_up") },
    { value: "volume_down",     label: t("action.volume_down") },
    { value: "anc_cycle",       label: t("action.anc_cycle") },
    { value: "voice_assistant", label: t("action.voice_assistant") },
    { value: "game_mode",       label: t("action.game_mode") },
    { value: "none",            label: t("action.none") }
  ];
}

function ancCycleOptions(tr) {
  var t = tr || function(k) { return k; };
  return [
    { value: "all",       label: t("cycle.all") },
    { value: "anc_trans", label: t("cycle.anc_trans") },
    { value: "anc_off",   label: t("cycle.anc_off") },
    { value: "trans_off", label: t("cycle.trans_off") }
  ];
}
