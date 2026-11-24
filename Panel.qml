import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: panel
  moduleName: "0xakadev.redmi-neo"
  ipcTarget: "0xakadev.redmi-neo"

  // ── State Properties ──
  property bool connected: false
  property bool rcspConnected: false
  property bool connecting: false
  property string deviceName: "REDMI Headphones Neo"
  property string deviceMac: ""
  property string saleModel: "M2546E1"
  property string firmware: ""
  property int battery: -1
  property bool charging: false
  property string batterySource: ""
  property int batteryLeft: -1
  property int batteryRight: -1
  property int batteryCase: -1
  property string codec: ""
  property string profile: ""

  // Noise Control
  property string anc: "off"
  property string ancLevel: "balanced"
  property string transLevel: "regular"
  property bool adaptiveAnc: false

  // Equalizer
  property string eq: "balanced"
  property var eqBands: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
  property var eqFrequencies: [62, 125, 250, 500, 1000, 2000, 4000, 8000, 12000, 16000]

  // Features & MFB Controls
  property bool multipoint: false
  property bool gameMode: false
  property bool autoAnswer: false
  property bool finding: false
  property var gestures: ({
    "single": "play_pause",
    "double": "next",
    "triple": "previous",
    "long": "anc_cycle"
  })
  property string ancCycle: "all"
  property string errorText: ""

  // UI Accordion & Async IPC Synchronization State
  property bool showGestures: false
  property int writeGen: 0
  property double lastWriteMs: 0
  property var cmdQueue: []

  readonly property string ctlBin: Qt.resolvedUrl("bin/redmictl").toString().replace(/^file:\/\//, "")
  readonly property color fgColor: bar ? bar.foreground : Commons.Color.foreground
  readonly property color accColor: Commons.Color.accent
  readonly property color urgColor: bar ? bar.urgent : Commons.Color.urgent
  readonly property string fontFam: bar ? bar.fontFamily : Style.font.family
  readonly property color dimColor: Qt.darker(fgColor, 1.45)
  readonly property color subtleBg: Qt.rgba(fgColor.r, fgColor.g, fgColor.b, 0.06)

  Commons.Translations {
    id: strings
    directory: Qt.resolvedUrl("i18n")
  }

  function tr(key) {
    return strings.t(key);
  }

  function applyState(raw) {
    var trimmed = (raw || "").trim();
    if (!trimmed) return;
    var lines = trimmed.split("\n");
    var lastLine = lines[lines.length - 1].trim();
    if (!lastLine) return;
    try {
      var s = JSON.parse(lastLine);
      if (s.connected !== undefined) panel.connected = Boolean(s.connected);
      if (s.rcsp_connected !== undefined) panel.rcspConnected = Boolean(s.rcsp_connected);
      if (s.connecting !== undefined) panel.connecting = Boolean(s.connecting);
      if (s.name) panel.deviceName = Model.neutralizeMarkup(s.name);
      if (s.mac !== undefined) panel.deviceMac = Model.neutralizeMarkup(s.mac || "");
      if (s.sale_model) panel.saleModel = Model.neutralizeMarkup(s.sale_model);
      panel.firmware = s.firmware ? Model.neutralizeMarkup(s.firmware) : "";

      panel.battery = (s.battery !== null && s.battery !== undefined) ? Number(s.battery) : -1;
      panel.charging = Boolean(s.charging);
      panel.batterySource = Model.neutralizeMarkup(s.battery_source || "");
      panel.batteryLeft = (s.battery_left !== null && s.battery_left !== undefined) ? Number(s.battery_left) : -1;
      panel.batteryRight = (s.battery_right !== null && s.battery_right !== undefined) ? Number(s.battery_right) : -1;
      panel.batteryCase = (s.battery_case !== null && s.battery_case !== undefined) ? Number(s.battery_case) : -1;

      panel.codec = s.codec ? Model.neutralizeMarkup(s.codec) : "";
      panel.profile = s.profile ? Model.neutralizeMarkup(s.profile) : "";

      if (s.anc) panel.anc = String(s.anc);
      if (s.anc_level) panel.ancLevel = String(s.anc_level);
      if (s.trans_level) panel.transLevel = String(s.trans_level);
      if (s.adaptive_anc !== undefined) panel.adaptiveAnc = Boolean(s.adaptive_anc);

      if (s.eq) panel.eq = String(s.eq);
      if (Array.isArray(s.eq_bands) && s.eq_bands.length === 10) {
        panel.eqBands = s.eq_bands.map(function(v) { return Math.round(Number(v) || 0); });
      }
      if (Array.isArray(s.eq_frequencies) && s.eq_frequencies.length === 10) {
        panel.eqFrequencies = s.eq_frequencies;
      }

      if (s.multipoint !== undefined) panel.multipoint = Boolean(s.multipoint);
      if (s.game_mode !== undefined) panel.gameMode = Boolean(s.game_mode);
      if (s.auto_answer !== undefined) panel.autoAnswer = Boolean(s.auto_answer);
      if (s.finding !== undefined) panel.finding = Boolean(s.finding);
      if (s.gestures && typeof s.gestures === "object") {
        panel.gestures = s.gestures;
      }
      if (s.anc_cycle) panel.ancCycle = String(s.anc_cycle);
      panel.errorText = s.error ? Model.neutralizeMarkup(s.error) : "";
    } catch (e) {
      // Ignore malformed output
    }
  }

  function _sameCmdTarget(a, b) {
    if (!a || !b || a.length === 0 || b.length === 0) return false;
    if (a[0] !== b[0]) return false;
    if (a[0] === "gesture") return a.length > 1 && b.length > 1 && a[1] === b[1];
    return true;
  }

  function _launchNextCmd() {
    if (cmdProc.running || panel.cmdQueue.length === 0) return;
    var q = panel.cmdQueue.slice();
    var next = q.shift();
    panel.cmdQueue = q;
    if (next[0] !== "poll" && next[0] !== "scan") {
      panel.lastWriteMs = Date.now();
    }
    cmdProc.startedGen = panel.writeGen;
    cmdProc.command = [panel.ctlBin].concat(next);
    cmdProc.running = true;
  }

  function pollState() {
    if (stateProc.running || cmdProc.running || panel.cmdQueue.length > 0) {
      return;
    }
    if ((Date.now() - panel.lastWriteMs) < 950) {
      return;
    }
    stateProc.startedGen = panel.writeGen;
    stateProc.command = [panel.ctlBin, "state"];
    stateProc.running = true;
  }

  function runCtl(args) {
    if (!args || args.length === 0) return;
    if (args.length === 1 && args[0] === "state") {
      pollState();
      return;
    }
    var isWrite = (args[0] !== "poll" && args[0] !== "scan");
    if (isWrite) {
      panel.writeGen = panel.writeGen + 1;
      panel.lastWriteMs = Date.now();
    }
    var q = panel.cmdQueue.slice();
    if (q.length > 0 && _sameCmdTarget(q[q.length - 1], args)) {
      q[q.length - 1] = args;
    } else {
      if (q.length >= 16) q.shift();
      q.push(args);
    }
    panel.cmdQueue = q;
    _launchNextCmd();
  }

  function refresh(forcePoll) {
    if (forcePoll) {
      runCtl(["poll"]);
    } else {
      pollState();
    }
  }

  function setAncMode(mode) {
    panel.anc = mode;
    runCtl(["anc", mode]);
  }

  function cycleAncMode() {
    var next = Model.nextAnc(panel.anc);
    setAncMode(next);
  }

  function setAncLevel(level) {
    panel.anc = "anc";
    panel.ancLevel = level;
    panel.adaptiveAnc = false;
    runCtl(["anc-level", level]);
  }

  function setTransLevel(level) {
    panel.anc = "transparency";
    panel.transLevel = level;
    runCtl(["trans-level", level]);
  }

  function setAdaptiveAnc(enabled) {
    if (enabled && panel.anc !== "anc") {
      panel.anc = "anc";
    }
    panel.adaptiveAnc = enabled;
    if (!enabled && panel.ancLevel === "adaptive") {
      panel.ancLevel = "balanced";
    }
    runCtl(["adaptive-anc", enabled ? "on" : "off"]);
  }

  function setEqPreset(preset) {
    panel.eq = preset;
    runCtl(["eq", preset]);
  }

  function setEqBand(index, gainDb) {
    var nextBands = panel.eqBands.slice();
    while (nextBands.length < 10) nextBands.push(0);
    nextBands[index] = Math.max(-6, Math.min(6, Math.round(gainDb)));
    panel.eqBands = nextBands;
    panel.eq = "custom";
    runCtl(["eq-bands", nextBands.join(",")]);
  }

  function resetEqBands() {
    panel.eqBands = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    runCtl(["eq-reset"]);
  }

  function toggleConnection() {
    panel.connecting = true;
    runCtl([panel.connected ? "disconnect" : "connect"]);
  }

  function cycleGestureAction(slot) {
    var opts = Model.gestureActionOptions(panel.tr, slot);
    var fallback = slot === "long" ? "anc_cycle" : "play_pause";
    var cur = (panel.gestures && panel.gestures[slot]) ? String(panel.gestures[slot]) : fallback;
    var idx = 0;
    for (var i = 0; i < opts.length; i++) {
      if (opts[i].value === cur) {
        idx = i;
        break;
      }
    }
    var nextVal = opts[(idx + 1) % opts.length].value;
    var updated = Object.assign({}, panel.gestures);
    updated[slot] = nextVal;
    panel.gestures = updated;
    runCtl(["gesture", slot, nextVal]);
  }

  function gestureActionLabel(slot) {
    var opts = Model.gestureActionOptions(panel.tr);
    var fallback = slot === "long" ? "anc_cycle" : "play_pause";
    var cur = (panel.gestures && panel.gestures[slot]) ? String(panel.gestures[slot]) : fallback;
    for (var i = 0; i < opts.length; i++) {
      if (opts[i].value === cur) return opts[i].label;
    }
    return cur;
  }

  Process {
    id: stateProc
    property int startedGen: 0
    command: [panel.ctlBin, "state"]
    stdout: StdioCollector {
      onStreamFinished: {
        if (stateProc.startedGen === panel.writeGen && !cmdProc.running && panel.cmdQueue.length === 0) {
          panel.applyState(text);
        }
      }
    }
  }

  Process {
    id: cmdProc
    property int startedGen: 0
    command: [panel.ctlBin, "state"]
    stdout: StdioCollector {
      onStreamFinished: {
        if (cmdProc.startedGen === panel.writeGen && panel.cmdQueue.length === 0) {
          panel.applyState(text);
        }
      }
    }
    onExited: {
      Qt.callLater(panel._launchNextCmd);
    }
  }

  Timer {
    interval: panel.opened ? 750 : 2000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: panel.pollState()
  }

  IpcHandler {
    target: "0xakadev.redmi-neo.anc"
    function set(mode: string): void {
      if (mode === "cycle") panel.cycleAncMode();
      else if (mode === "on" || mode === "anc") panel.setAncMode("anc");
      else if (mode === "off") panel.setAncMode("off");
      else if (mode === "transparency") panel.setAncMode("transparency");
    }
  }

  implicitWidth: barBtn.implicitWidth
  implicitHeight: barBtn.implicitHeight

  // ── Bar Trigger Widget ──
  BarIconButton {
    id: barBtn
    anchors.fill: parent
    bar: panel.bar
    active: panel.opened || panel.connected
    text: Model.barIcon(panel.connected, panel.anc, panel.finding)
    tooltipText: {
      var status = panel.connected
        ? (panel.tr("status.connected") + (panel.battery >= 0 ? (" · " + Model.formatBattery(panel.battery)) : ""))
        : panel.tr("status.disconnected");
      return Model.neutralizeMarkup(panel.deviceName + " — " + status);
    }
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton || buttonCode === Qt.MiddleButton) {
        if (panel.connected) panel.cycleAncMode();
        else panel.toggleConnection();
      } else {
        panel.toggle();
        if (panel.opened) panel.refresh(false);
      }
    }
  }

  // ── Popup Panel Overlay ──
  KeyboardPanel {
    id: kbPanel
    anchorItem: barBtn
    owner: panel
    bar: panel.bar
    open: panel.opened
    focusTarget: keyCatcher
    contentWidth: kbPanel.fittedContentWidth(Style.space(385))
    contentHeight: kbPanel.fittedContentHeight(column.implicitHeight, Style.space(740))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent

      onCloseRequested: panel.close()
      onTabRequested: function(direction) { panel.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "n" || t === "N") {
          panel.cycleAncMode();
        } else if (t === "r" || t === "R") {
          panel.refresh(true);
        } else if (t === "c" || t === "C") {
          panel.toggleConnection();
        }
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.spacing.md

    // ── Header Row ──
    Item {
      width: parent.width
      height: Math.max(headerCol.implicitHeight, headerActions.implicitHeight)

      Row {
        id: headerLeft
        anchors.left: parent.left
        anchors.right: headerActions.left
        anchors.rightMargin: Style.spacing.sm
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.spacing.md

        Text {
          textFormat: Text.PlainText
          anchors.verticalCenter: parent.verticalCenter
          text: Model.barIcon(panel.connected, panel.anc, panel.finding)
          color: panel.connected ? panel.accColor : panel.dimColor
          font.family: panel.fontFam
          font.pixelSize: Style.font.iconLarge
        }

        Column {
          id: headerCol
          width: Math.max(0, parent.width - Style.space(34))
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(2)

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: Model.neutralizeMarkup(panel.deviceName || panel.tr("title"))
            color: panel.fgColor
            font.family: panel.fontFam
            font.pixelSize: Style.font.heading
            font.bold: true
            elide: Text.ElideRight
          }

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: {
              var parts = [];
              if (panel.connecting) parts.push(panel.tr("status.connecting"));
              else if (panel.connected) parts.push(panel.tr("status.connected"));
              else parts.push(panel.tr("status.disconnected"));

              if (panel.connected && panel.codec !== "") parts.push(panel.codec);
              if (panel.saleModel !== "") parts.push(panel.saleModel);
              if (panel.firmware !== "") parts.push("v" + panel.firmware);
              return Model.neutralizeMarkup(parts.join(" · "));
            }
            color: panel.connected ? panel.accColor : panel.dimColor
            font.family: panel.fontFam
            font.pixelSize: Style.font.caption
            font.bold: true
            elide: Text.ElideRight
          }
        }
      }

      Row {
        id: headerActions
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.spacing.xs

        Button {
          text: Model.neutralizeMarkup(
            panel.connecting
              ? panel.tr("status.connecting")
              : (panel.connected ? panel.tr("action.disconnect") : panel.tr("action.connect"))
          )
          iconText: panel.connecting ? "󰑐" : (panel.connected ? "󰂲" : "󰂯")
          iconSpinning: panel.connecting
          bordered: true
          selected: panel.connected
          foreground: panel.fgColor
          accent: panel.accColor
          fontFamily: panel.fontFam
          fontSize: Style.font.caption
          horizontalPadding: Style.spacing.sm
          verticalPadding: Style.space(4)
          onClicked: panel.toggleConnection()
        }

        PanelActionButton {
          iconText: "󰑐"
          tooltipText: Model.neutralizeMarkup(panel.tr("action.refresh"))
          foreground: panel.fgColor
          fontFamily: panel.fontFam
          onClicked: panel.refresh(true)
        }
      }
    }

    // ── Battery Status Card ──
    Rectangle {
      visible: panel.battery >= 0
      width: parent.width
      height: battCardCol.implicitHeight + Style.spacing.md * 2
      radius: Style.cornerRadius
      color: panel.subtleBg

      Column {
        id: battCardCol
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Style.spacing.md
        anchors.rightMargin: Style.spacing.md
        spacing: Style.spacing.xs

        Row {
          width: parent.width
          spacing: Style.spacing.sm

          Text {
            textFormat: Text.PlainText
            anchors.verticalCenter: parent.verticalCenter
            text: Model.batteryGlyph(panel.battery, panel.charging)
            color: (panel.battery >= 0 && panel.battery <= 15 && !panel.charging)
              ? panel.urgColor
              : (panel.charging ? panel.accColor : panel.fgColor)
            font.family: panel.fontFam
            font.pixelSize: Style.font.icon
          }

          Text {
            textFormat: Text.PlainText
            anchors.verticalCenter: parent.verticalCenter
            text: Model.neutralizeMarkup(panel.tr("battery.headset") + "  " + Model.formatBattery(panel.battery))
            color: panel.fgColor
            font.family: panel.fontFam
            font.pixelSize: Style.font.body
            font.bold: true
          }

          Text {
            textFormat: Text.PlainText
            anchors.verticalCenter: parent.verticalCenter
            visible: panel.charging || !panel.connected || panel.rcspConnected
            text: Model.neutralizeMarkup(
              panel.charging
                ? ("· " + panel.tr("battery.charging"))
                : (!panel.connected
                    ? ("· " + panel.tr("status.beacon"))
                    : (panel.rcspConnected ? ("· " + panel.tr("status.rcsp_live")) : ""))
            )
            color: panel.charging ? panel.accColor : panel.dimColor
            font.family: panel.fontFam
            font.pixelSize: Style.font.caption
          }
        }

        // Smooth Battery Level Progress Bar
        Rectangle {
          width: parent.width
          height: Style.space(6)
          radius: height / 2
          color: Qt.rgba(panel.fgColor.r, panel.fgColor.g, panel.fgColor.b, 0.14)

          Rectangle {
            width: parent.width * Math.max(0, Math.min(1, panel.battery / 100.0))
            height: parent.height
            radius: parent.radius
            color: (panel.battery <= 15 && !panel.charging)
              ? panel.urgColor
              : panel.accColor

            Behavior on width {
              NumberAnimation {
                duration: Style.duration(180)
                easing.type: Easing.OutCubic
              }
            }
          }
        }

        // Optional Left / Right / Case breakdown if reported
        Row {
          visible: panel.batteryLeft >= 0 || panel.batteryRight >= 0 || panel.batteryCase >= 0
          spacing: Style.spacing.lg

          Text {
            textFormat: Text.PlainText
            visible: panel.batteryLeft >= 0
            text: Model.neutralizeMarkup(panel.tr("battery.left") + ": " + Model.formatBattery(panel.batteryLeft))
            color: panel.dimColor
            font.family: panel.fontFam
            font.pixelSize: Style.font.caption
          }

          Text {
            textFormat: Text.PlainText
            visible: panel.batteryRight >= 0
            text: Model.neutralizeMarkup(panel.tr("battery.right") + ": " + Model.formatBattery(panel.batteryRight))
            color: panel.dimColor
            font.family: panel.fontFam
            font.pixelSize: Style.font.caption
          }

          Text {
            textFormat: Text.PlainText
            visible: panel.batteryCase >= 0
            text: Model.neutralizeMarkup(panel.tr("battery.case") + ": " + Model.formatBattery(panel.batteryCase))
            color: panel.dimColor
            font.family: panel.fontFam
            font.pixelSize: Style.font.caption
          }
        }
      }
    }

    PanelSeparator { foreground: panel.fgColor }

    // ── Noise Control (ANC / Transparency / Off) ──
    PanelSectionHeader {
      text: Model.neutralizeMarkup(panel.tr("section.anc"))
      foreground: panel.fgColor
      fontFamily: panel.fontFam
    }

    ButtonGroup {
      width: parent.width
      options: Model.ancOptions(panel.tr)
      value: panel.anc
      foreground: panel.fgColor
      accent: panel.accColor
      fontFamily: panel.fontFam
      onChanged: function(v) { panel.setAncMode(v); }
    }

    // ANC Strength Level (Light / Balanced / Deep) + Adaptive ANC when ANC is active
    Column {
      visible: panel.anc === "anc"
      width: parent.width
      spacing: Style.spacing.sm

      PanelSectionHeader {
        text: Model.neutralizeMarkup(panel.tr("section.anc_level"))
        foreground: panel.fgColor
        fontFamily: panel.fontFam
      }

      ButtonGroup {
        width: parent.width
        options: Model.ancLevelOptions(panel.tr)
        value: panel.ancLevel
        foreground: panel.fgColor
        accent: panel.accColor
        fontFamily: panel.fontFam
        onChanged: function(v) { panel.setAncLevel(v); }
      }

      Toggle {
        width: parent.width
        label: Model.neutralizeMarkup(panel.tr("anc.adaptive"))
        checked: panel.adaptiveAnc
        foreground: panel.fgColor
        accent: panel.accColor
        fontFamily: panel.fontFam
        onClicked: panel.setAdaptiveAnc(!panel.adaptiveAnc)
      }
    }

    // Transparency Sub-Mode (Regular / Enhance Voice / Ambient) when Transparency is active
    Column {
      visible: panel.anc === "transparency"
      width: parent.width
      spacing: Style.spacing.sm

      PanelSectionHeader {
        text: Model.neutralizeMarkup(panel.tr("section.trans_level"))
        foreground: panel.fgColor
        fontFamily: panel.fontFam
      }

      ButtonGroup {
        width: parent.width
        options: Model.transLevelOptions(panel.tr)
        value: panel.transLevel
        foreground: panel.fgColor
        accent: panel.accColor
        fontFamily: panel.fontFam
        onChanged: function(v) { panel.setTransLevel(v); }
      }
    }

    PanelSeparator { foreground: panel.fgColor }

    // ── Equalizer Presets & 10-Band Custom EQ ──
    PanelSectionHeader {
      text: Model.neutralizeMarkup(panel.tr("section.eq"))
      foreground: panel.fgColor
      fontFamily: panel.fontFam
    }

    Column {
      width: parent.width
      spacing: Style.space(5)

      Flickable {
        id: eqFlick
        width: parent.width
        height: eqBtnGroup.implicitHeight
        contentWidth: Math.max(width, eqBtnGroup.implicitWidth)
        contentHeight: height
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.HorizontalFlick
        interactive: contentWidth > width

        WheelHandler {
          onWheel: function(event) {
            if (eqFlick.contentWidth <= eqFlick.width) return;
            var delta = event.angleDelta.x !== 0 ? event.angleDelta.x : event.angleDelta.y;
            var maxScroll = Math.max(0, eqFlick.contentWidth - eqFlick.width);
            eqFlick.contentX = Math.max(0, Math.min(maxScroll, eqFlick.contentX - delta * 0.5));
            event.accepted = true;
          }
        }

        ButtonGroup {
          id: eqBtnGroup
          spacing: Style.spacing.xs
          options: Model.eqOptions(panel.tr)
          value: panel.eq
          foreground: panel.fgColor
          accent: panel.accColor
          fontFamily: panel.fontFam
          onChanged: function(v) { panel.setEqPreset(v); }
        }
      }

      // Interactive horizontal scrollbar for Equalizer presets
      Rectangle {
        id: eqScrollTrack
        visible: eqFlick.contentWidth > eqFlick.width
        width: parent.width
        height: Style.space(5)
        radius: height / 2
        color: Qt.rgba(panel.fgColor.r, panel.fgColor.g, panel.fgColor.b, 0.14)

        Rectangle {
          id: eqScrollThumb
          width: parent.width * Math.max(0.2, Math.min(1, eqFlick.width / Math.max(1, eqFlick.contentWidth)))
          height: parent.height
          radius: parent.radius
          x: (parent.width - width) * (eqFlick.contentX / Math.max(1, eqFlick.contentWidth - eqFlick.width))
          color: panel.accColor
        }

        MouseArea {
          anchors.fill: parent
          anchors.topMargin: -Style.space(4)
          anchors.bottomMargin: -Style.space(4)
          function seekTo(mouseX) {
            var maxScroll = Math.max(0, eqFlick.contentWidth - eqFlick.width);
            var ratio = Math.max(0, Math.min(1, (mouseX - eqScrollThumb.width / 2) / Math.max(1, eqScrollTrack.width - eqScrollThumb.width)));
            eqFlick.contentX = ratio * maxScroll;
          }
          onPressed: function(mouse) { seekTo(mouse.x); }
          onPositionChanged: function(mouse) { if (pressed) seekTo(mouse.x); }
        }
      }
    }

    // 10-Band Custom Equalizer Sliders (shown when Custom EQ is selected)
    Column {
      visible: panel.eq === "custom"
      width: parent.width
      spacing: Style.spacing.xs

      Item {
        width: parent.width
        height: Math.max(eqHeaderLabel.implicitHeight, eqResetBtn.implicitHeight)

        Text {
          id: eqHeaderLabel
          textFormat: Text.PlainText
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          text: Model.neutralizeMarkup(panel.tr("eq.custom_bands"))
          color: panel.dimColor
          font.family: panel.fontFam
          font.pixelSize: Style.font.caption
          font.bold: true
        }

        Button {
          id: eqResetBtn
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          text: Model.neutralizeMarkup(panel.tr("eq.reset"))
          bordered: true
          foreground: panel.fgColor
          accent: panel.accColor
          fontFamily: panel.fontFam
          fontSize: Style.font.caption
          horizontalPadding: Style.spacing.sm
          verticalPadding: Style.space(2)
          onClicked: panel.resetEqBands()
        }
      }

      Repeater {
        model: 10

        Item {
          required property int index
          width: parent.width
          height: Style.space(22)

          readonly property int bandFreq: (panel.eqFrequencies && index < panel.eqFrequencies.length)
            ? Number(panel.eqFrequencies[index])
            : 1000
          readonly property int bandGain: (panel.eqBands && index < panel.eqBands.length)
            ? Number(panel.eqBands[index])
            : 0

          Text {
            id: freqText
            textFormat: Text.PlainText
            width: Style.space(36)
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: Model.neutralizeMarkup(Model.formatFreqLabel(parent.bandFreq) + "Hz")
            color: panel.dimColor
            font.family: panel.fontFam
            font.pixelSize: Style.font.caption
          }

          PanelSlider {
            id: bandSlider
            bar: panel.bar
            anchors.left: freqText.right
            anchors.right: gainText.left
            anchors.leftMargin: Style.spacing.xs
            anchors.rightMargin: Style.spacing.xs
            anchors.verticalCenter: parent.verticalCenter
            minimum: -6
            maximum: 6
            step: 1
            integer: true
            tickCount: 13
            value: parent.bandGain
            onReleased: function(v) {
              panel.setEqBand(parent.index, v);
            }
            onRightClicked: {
              panel.setEqBand(parent.index, 0);
            }
          }

          Text {
            id: gainText
            textFormat: Text.PlainText
            width: Style.space(42)
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            horizontalAlignment: Text.AlignRight
            text: Model.neutralizeMarkup(Model.formatGainDb(bandSlider.dragging ? bandSlider.liveValue : parent.bandGain))
            color: panel.fgColor
            font.family: panel.fontFam
            font.pixelSize: Style.font.caption
          }
        }
      }
    }

    PanelSeparator { foreground: panel.fgColor }

    // ── Device Features & Controls ──
    PanelSectionHeader {
      text: Model.neutralizeMarkup(panel.tr("section.features"))
      foreground: panel.fgColor
      fontFamily: panel.fontFam
    }

    Toggle {
      width: parent.width
      label: Model.neutralizeMarkup(panel.tr("feature.multipoint"))
      checked: panel.multipoint
      foreground: panel.fgColor
      accent: panel.accColor
      fontFamily: panel.fontFam
      onClicked: {
        panel.multipoint = !panel.multipoint;
        panel.runCtl(["multipoint", panel.multipoint ? "on" : "off"]);
      }
    }

    Toggle {
      width: parent.width
      label: Model.neutralizeMarkup(panel.tr("feature.game_mode"))
      checked: panel.gameMode
      foreground: panel.fgColor
      accent: panel.accColor
      fontFamily: panel.fontFam
      onClicked: {
        panel.gameMode = !panel.gameMode;
        panel.runCtl(["game-mode", panel.gameMode ? "on" : "off"]);
      }
    }

    Toggle {
      width: parent.width
      label: Model.neutralizeMarkup(panel.tr("feature.auto_answer"))
      checked: panel.autoAnswer
      foreground: panel.fgColor
      accent: panel.accColor
      fontFamily: panel.fontFam
      onClicked: {
        panel.autoAnswer = !panel.autoAnswer;
        panel.runCtl(["auto-answer", panel.autoAnswer ? "on" : "off"]);
      }
    }

    // Find My Headphones Row
    Item {
      width: parent.width
      height: Math.max(findLabel.implicitHeight, findBtn.implicitHeight)

      Text {
        id: findLabel
        textFormat: Text.PlainText
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: Model.neutralizeMarkup(panel.tr("feature.find"))
        color: panel.fgColor
        font.family: panel.fontFam
        font.pixelSize: Style.font.body
      }

      Button {
        id: findBtn
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        iconText: panel.finding ? "󰂞" : "󰂜"
        text: Model.neutralizeMarkup(panel.finding ? panel.tr("feature.find_stop") : panel.tr("feature.find_ring"))
        selected: panel.finding
        bordered: true
        foreground: panel.fgColor
        accent: panel.accColor
        fontFamily: panel.fontFam
        fontSize: Style.font.caption
        horizontalPadding: Style.spacing.sm
        verticalPadding: Style.space(4)
        onClicked: {
          panel.finding = !panel.finding;
          panel.runCtl(["find", panel.finding ? "on" : "off"]);
        }
      }
    }

    PanelSeparator { foreground: panel.fgColor }

    // ── Collapsible MFB Button Controls Section ──
    Button {
      width: parent.width
      leftAlign: true
      iconText: panel.showGestures ? "󰅀" : "󰅂"
      text: Model.neutralizeMarkup(panel.tr("section.gestures"))
      foreground: panel.fgColor
      accent: panel.accColor
      fontFamily: panel.fontFam
      fontSize: Style.font.caption
      onClicked: panel.showGestures = !panel.showGestures
    }

    Column {
      visible: panel.showGestures
      width: parent.width
      spacing: Style.spacing.xs

      Repeater {
        model: Model.gestureSlots(panel.tr)

        Item {
          required property var modelData
          width: parent.width
          height: Math.max(slotText.implicitHeight, slotBtn.implicitHeight)

          Text {
            id: slotText
            textFormat: Text.PlainText
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: Model.neutralizeMarkup(modelData.label)
            color: panel.dimColor
            font.family: panel.fontFam
            font.pixelSize: Style.font.bodySmall
          }

          Button {
            id: slotBtn
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            bordered: true
            text: Model.neutralizeMarkup(panel.gestureActionLabel(modelData.key))
            foreground: panel.fgColor
            accent: panel.accColor
            fontFamily: panel.fontFam
            fontSize: Style.font.caption
            horizontalPadding: Style.spacing.sm
            verticalPadding: Style.space(3)
            onClicked: panel.cycleGestureAction(modelData.key)
          }
        }
      }

      // Long-press ANC cycle modes
      Text {
        textFormat: Text.PlainText
        text: Model.neutralizeMarkup(panel.tr("gesture.anc_cycle"))
        color: panel.dimColor
        font.family: panel.fontFam
        font.pixelSize: Style.font.caption
      }

      Column {
        width: parent.width
        spacing: Style.space(5)

        Flickable {
          id: ancCycleFlick
          width: parent.width
          height: ancCycleBtnGroup.implicitHeight
          contentWidth: Math.max(width, ancCycleBtnGroup.implicitWidth)
          contentHeight: height
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          flickableDirection: Flickable.HorizontalFlick
          interactive: contentWidth > width

          WheelHandler {
            onWheel: function(event) {
              if (ancCycleFlick.contentWidth <= ancCycleFlick.width) return;
              var delta = event.angleDelta.x !== 0 ? event.angleDelta.x : event.angleDelta.y;
              var maxScroll = Math.max(0, ancCycleFlick.contentWidth - ancCycleFlick.width);
              ancCycleFlick.contentX = Math.max(0, Math.min(maxScroll, ancCycleFlick.contentX - delta * 0.5));
              event.accepted = true;
            }
          }

          ButtonGroup {
            id: ancCycleBtnGroup
            spacing: Style.spacing.xs
            options: Model.ancCycleOptions(panel.tr)
            value: panel.ancCycle
            foreground: panel.fgColor
            accent: panel.accColor
            fontFamily: panel.fontFam
            onChanged: function(v) {
              panel.ancCycle = v;
              panel.runCtl(["anc-cycle", v]);
            }
          }
        }

        // Interactive horizontal scrollbar for Long-press ANC cycle modes
        Rectangle {
          id: ancCycleScrollTrack
          visible: ancCycleFlick.contentWidth > ancCycleFlick.width
          width: parent.width
          height: Style.space(5)
          radius: height / 2
          color: Qt.rgba(panel.fgColor.r, panel.fgColor.g, panel.fgColor.b, 0.14)

          Rectangle {
            id: ancCycleScrollThumb
            width: parent.width * Math.max(0.2, Math.min(1, ancCycleFlick.width / Math.max(1, ancCycleFlick.contentWidth)))
            height: parent.height
            radius: parent.radius
            x: (parent.width - width) * (ancCycleFlick.contentX / Math.max(1, ancCycleFlick.contentWidth - ancCycleFlick.width))
            color: panel.accColor
          }

          MouseArea {
            anchors.fill: parent
            anchors.topMargin: -Style.space(4)
            anchors.bottomMargin: -Style.space(4)
            function seekTo(mouseX) {
              var maxScroll = Math.max(0, ancCycleFlick.contentWidth - ancCycleFlick.width);
              var ratio = Math.max(0, Math.min(1, (mouseX - ancCycleScrollThumb.width / 2) / Math.max(1, ancCycleScrollTrack.width - ancCycleScrollThumb.width)));
              ancCycleFlick.contentX = ratio * maxScroll;
            }
            onPressed: function(mouse) { seekTo(mouse.x); }
            onPositionChanged: function(mouse) { if (pressed) seekTo(mouse.x); }
          }
        }
      }
    }

    // ── Error Banner (if any) ──
    Text {
      textFormat: Text.PlainText
      visible: panel.errorText !== ""
      width: parent.width
      text: Model.neutralizeMarkup(panel.errorText)
      color: panel.urgColor
      font.family: panel.fontFam
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }
        }
      }
    }
  }
}
