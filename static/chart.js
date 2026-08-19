/* ===========================================================================
   Basit SVG çizgi grafiği — dış kütüphane yok, internet gerektirmez.

   Tasarım kuralları (dataviz rehberinden, gözle değil ölçüyle seçildi):
     · 2px çizgi, yuvarlak birleşim/uç
     · Alan dolgusu serinin renginde ~%10 opaklık — asla doygun blok
     · Izgara ve eksen 1px DÜZ (kesikli değil), yüzeyden bir ton uzak
     · Uç noktada ≥8px işaretçi, çevresinde 2px yüzey rengi halka
     · Crosshair X'e kilitleniyor — okuyucu 2px çizgiye nişan almak zorunda değil
     · Her noktaya sayı yazılmıyor; yalnızca son değer doğrudan etiketleniyor
     · Tooltip zorunlu değil: aynı veriler tablo görünümünde de var
   =========================================================================== */

(function (global) {
  "use strict";

  const CSS = getComputedStyle(document.documentElement);
  const color = (name, fallback) =>
    (CSS.getPropertyValue(name) || "").trim() || fallback;

  const PALETTE = {
    series:  color("--series-1", "#3987e5"),
    grid:    color("--grid", "#2c2c2a"),
    axis:    color("--axis", "#383835"),
    ink:     color("--ink", "#ffffff"),
    ink2:    color("--ink-2", "#c3c2b7"),
    muted:   color("--ink-muted", "#898781"),
    surface: color("--surface", "#1a1a19"),
  };

  const SVG_NS = "http://www.w3.org/2000/svg";
  const PAD = { top: 18, right: 68, bottom: 30, left: 62 };

  function el(name, attrs) {
    const node = document.createElementNS(SVG_NS, name);
    for (const key in attrs) node.setAttribute(key, attrs[key]);
    return node;
  }

  /* --- Eksen için "güzel" sayı adımları (0 / 250 / 500 …) ---------------- */
  function niceStep(range, targetTicks) {
    const rough = range / Math.max(1, targetTicks);
    const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
    const normalized = rough / magnitude;
    let step;
    if (normalized <= 1) step = 1;
    else if (normalized <= 2) step = 2;
    else if (normalized <= 2.5) step = 2.5;
    else if (normalized <= 5) step = 5;
    else step = 10;
    return step * magnitude;
  }

  function formatNumber(value, decimals) {
    if (value === null || value === undefined || Number.isNaN(value)) return "—";
    return value.toLocaleString("tr-TR", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }

  function formatDate(iso, interval) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const intraday = interval && (interval.includes("m") || interval === "1h");
    return intraday
      ? d.toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString("tr-TR", { day: "2-digit", month: "short", year: "2-digit" });
  }

  /* ====================================================================== */

  function LineChart(container, options) {
    options = options || {};
    const tooltip = options.tooltip;
    const onHover = options.onHover;

    let candles = [];
    let interval = "1d";
    let svg = null;
    let geometry = null;

    /* --- Çizim ---------------------------------------------------------- */
    function draw() {
      container.textContent = "";
      if (!candles.length) return;

      const width = container.clientWidth || 720;
      const height = options.height || 320;

      svg = el("svg", {
        width: "100%",
        height: String(height),
        viewBox: `0 0 ${width} ${height}`,
        role: "img",
        "aria-label": options.ariaLabel || "Fiyat grafiği",
      });

      const plotW = width - PAD.left - PAD.right;
      const plotH = height - PAD.top - PAD.bottom;

      const values = candles.map(c => c.c);
      let min = Math.min.apply(null, values);
      let max = Math.max.apply(null, values);
      if (min === max) { min -= 1; max += 1; }          // düz seri
      const headroom = (max - min) * 0.08;
      min -= headroom; max += headroom;

      const step = niceStep(max - min, 4);
      const tickStart = Math.ceil(min / step) * step;

      const x = i => PAD.left + (candles.length === 1 ? plotW / 2 : (i / (candles.length - 1)) * plotW);
      const y = v => PAD.top + plotH - ((v - min) / (max - min)) * plotH;

      geometry = { x, y, plotW, plotH, width, height };

      // Ondalık basamak: küçük fiyatlarda daha hassas
      const decimals = max < 10 ? 3 : 2;

      /* --- Izgara + Y ekseni etiketleri (düz hairline) --- */
      for (let v = tickStart; v <= max; v += step) {
        const yy = y(v);
        svg.appendChild(el("line", {
          x1: PAD.left, x2: width - PAD.right, y1: yy, y2: yy,
          stroke: PALETTE.grid, "stroke-width": 1,
        }));
        const label = el("text", {
          x: PAD.left - 10, y: yy + 4,
          "text-anchor": "end", fill: PALETTE.muted,
          "font-size": "11", "font-variant-numeric": "tabular-nums",
        });
        label.textContent = formatNumber(v, decimals);
        svg.appendChild(label);
      }

      /* --- Taban ekseni --- */
      svg.appendChild(el("line", {
        x1: PAD.left, x2: width - PAD.right,
        y1: PAD.top + plotH, y2: PAD.top + plotH,
        stroke: PALETTE.axis, "stroke-width": 1,
      }));

      /* --- X ekseni etiketleri (en fazla 5, çakışmasın) --- */
      const labelCount = Math.min(5, candles.length);
      for (let i = 0; i < labelCount; i++) {
        const index = Math.round((i / Math.max(1, labelCount - 1)) * (candles.length - 1));
        const anchor = i === 0 ? "start" : (i === labelCount - 1 ? "end" : "middle");
        const label = el("text", {
          x: x(index), y: height - 10,
          "text-anchor": anchor, fill: PALETTE.muted, "font-size": "11",
        });
        label.textContent = formatDate(candles[index].t, interval);
        svg.appendChild(label);
      }

      /* --- Alan dolgusu (%10 opaklık — yıkama, blok değil) --- */
      let areaPath = `M ${x(0)} ${PAD.top + plotH}`;
      candles.forEach((c, i) => { areaPath += ` L ${x(i)} ${y(c.c)}`; });
      areaPath += ` L ${x(candles.length - 1)} ${PAD.top + plotH} Z`;
      svg.appendChild(el("path", {
        d: areaPath, fill: PALETTE.series, "fill-opacity": "0.10", stroke: "none",
      }));

      /* --- Çizgi (2px, yuvarlak) --- */
      let linePath = "";
      candles.forEach((c, i) => { linePath += (i ? " L " : "M ") + x(i) + " " + y(c.c); });
      svg.appendChild(el("path", {
        d: linePath, fill: "none", stroke: PALETTE.series,
        "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round",
      }));

      /* --- Son nokta: yüzey rengi halka + işaretçi (≥8px) --- */
      const lastIndex = candles.length - 1;
      const lastValue = candles[lastIndex].c;
      svg.appendChild(el("circle", {
        cx: x(lastIndex), cy: y(lastValue), r: 6,
        fill: PALETTE.surface,
      }));
      svg.appendChild(el("circle", {
        cx: x(lastIndex), cy: y(lastValue), r: 4, fill: PALETTE.series,
      }));

      /* --- Yalnızca son değer doğrudan etiketleniyor --- */
      const endLabel = el("text", {
        x: x(lastIndex) + 12, y: y(lastValue) + 4,
        fill: PALETTE.ink, "font-size": "12.5", "font-weight": "650",
        "font-variant-numeric": "tabular-nums",
      });
      endLabel.textContent = formatNumber(lastValue, decimals);
      svg.appendChild(endLabel);

      /* --- Crosshair katmanı --- */
      const crosshair = el("line", {
        y1: PAD.top, y2: PAD.top + plotH,
        stroke: PALETTE.axis, "stroke-width": 1, opacity: "0",
      });
      const focusRing = el("circle", { r: 6, fill: PALETTE.surface, opacity: "0" });
      const focusDot = el("circle", { r: 4, fill: PALETTE.series, opacity: "0" });
      svg.append(crosshair, focusRing, focusDot);

      /* --- Fare/klavye yakalayıcı: tüm çizim alanı hedef --- */
      const capture = el("rect", {
        x: PAD.left, y: PAD.top, width: plotW, height: plotH,
        fill: "transparent", tabindex: "0",
        role: "application", "aria-label": "Grafikte gezinmek için ok tuşlarını kullan",
      });
      svg.appendChild(capture);

      let cursor = -1;

      function showAt(index) {
        if (index < 0 || index >= candles.length) return;
        cursor = index;
        const candle = candles[index];
        const cx = x(index), cy = y(candle.c);

        crosshair.setAttribute("x1", cx);
        crosshair.setAttribute("x2", cx);
        crosshair.setAttribute("opacity", "1");
        [focusRing, focusDot].forEach(node => {
          node.setAttribute("cx", cx);
          node.setAttribute("cy", cy);
          node.setAttribute("opacity", "1");
        });

        if (tooltip) {
          tooltip.hidden = false;
          tooltip.textContent = "";

          const value = document.createElement("div");
          value.style.cssText = "font-size:15px;font-weight:650;font-variant-numeric:tabular-nums;";
          value.textContent = formatNumber(candle.c, decimals) + " TL";

          const when = document.createElement("div");
          when.style.cssText = "font-size:11.5px;color:" + PALETTE.muted + ";margin-top:2px;";
          when.textContent = formatDate(candle.t, interval);

          tooltip.append(value, when);

          if (candle.v) {
            const vol = document.createElement("div");
            vol.style.cssText = "font-size:11.5px;color:" + PALETTE.ink2 + ";margin-top:4px;";
            vol.textContent = "Hacim " + formatNumber(candle.v, 0);
            tooltip.appendChild(vol);
          }

          // Tooltip'i crosshair'in yanına koy — üstüne DEĞİL.
          // Genişliği ölçerek yerleştiriyoruz; sabit bir tahmin (150px)
          // gerçek genişlikten dar kalıp kutucuğun çizgiyi örtmesine yol açıyordu.
          const gap = 14;
          const tipW = tooltip.offsetWidth || 150;
          const tipH = tooltip.offsetHeight || 60;
          let left = cx + gap;
          if (left + tipW > width - 4) left = cx - gap - tipW;   // sağa sığmıyorsa sola al
          if (left < 4) left = 4;

          // Dikeyde noktanın hizasında dursun ama çizim alanından taşmasın
          let top = cy - tipH / 2;
          top = Math.max(4, Math.min(top, height - tipH - 4));

          tooltip.style.left = left + "px";
          tooltip.style.top = top + "px";
        }

        if (onHover) onHover(candle, index);
      }

      function hide() {
        cursor = -1;
        crosshair.setAttribute("opacity", "0");
        focusRing.setAttribute("opacity", "0");
        focusDot.setAttribute("opacity", "0");
        if (tooltip) tooltip.hidden = true;
        if (onHover) onHover(null, -1);
      }

      function indexFromEvent(ev) {
        const rect = svg.getBoundingClientRect();
        const scale = width / rect.width;                 // viewBox ölçeği
        const px = (ev.clientX - rect.left) * scale;
        const ratio = (px - PAD.left) / plotW;
        return Math.max(0, Math.min(candles.length - 1, Math.round(ratio * (candles.length - 1))));
      }

      capture.addEventListener("pointermove", ev => showAt(indexFromEvent(ev)));
      capture.addEventListener("pointerleave", hide);
      capture.addEventListener("focus", () => showAt(candles.length - 1));
      capture.addEventListener("blur", hide);
      capture.addEventListener("keydown", ev => {
        if (ev.key === "ArrowRight") { ev.preventDefault(); showAt(Math.min(candles.length - 1, (cursor < 0 ? candles.length - 1 : cursor) + 1)); }
        else if (ev.key === "ArrowLeft") { ev.preventDefault(); showAt(Math.max(0, (cursor < 0 ? candles.length - 1 : cursor) - 1)); }
        else if (ev.key === "Home") { ev.preventDefault(); showAt(0); }
        else if (ev.key === "End") { ev.preventDefault(); showAt(candles.length - 1); }
        else if (ev.key === "Escape") { hide(); }
      });

      container.appendChild(svg);
    }

    /* --- Genel arayüz ---------------------------------------------------- */
    let resizeTimer;
    const onResize = () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(draw, 120);
    };
    global.addEventListener("resize", onResize);

    return {
      setData(newCandles, newInterval) {
        candles = (newCandles || []).filter(c => c && typeof c.c === "number");
        interval = newInterval || "1d";
        draw();
      },
      clear() {
        candles = [];
        container.textContent = "";
      },
      get length() { return candles.length; },
    };
  }

  global.LineChart = LineChart;
  global.chartFormat = { number: formatNumber, date: formatDate };
})(window);
