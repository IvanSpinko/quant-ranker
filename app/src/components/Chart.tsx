// Thin uPlot wrapper: time series with themed colours, resized with its container.
import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

export interface Line {
  label: string;
  values: (number | null)[];
  /** CSS variable name, e.g. "--c-net". */
  color: string;
  fill?: boolean;
  dash?: boolean;
}

interface Props {
  /** Seconds since the epoch. */
  x: number[];
  lines: Line[];
  height?: number;
  log?: boolean;
  format?: (v: number) => string;
  /** Horizontal reference line, e.g. 0 or 1. */
  baseline?: number;
}

const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";

export function Chart({ x, lines, height = 280, log = false, format, baseline }: Props) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = host.current;
    if (!el || x.length === 0) return;
    const axis = cssVar("--muted");
    const grid = cssVar("--grid");
    const fmt = format ?? ((v: number) => v.toFixed(2));
    const opts: uPlot.Options = {
      width: el.clientWidth,
      height,
      legend: { show: true },
      cursor: { drag: { x: true, y: false } },
      scales: { x: { time: true }, y: log ? { distr: 3 } : {} },
      axes: [
        { stroke: axis, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid } },
        { stroke: axis, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, size: 64, values: (_u, vals) => vals.map((v) => (v == null ? "" : fmt(v))) },
      ],
      series: [
        {},
        ...lines.map((l) => {
          const color = cssVar(l.color);
          return {
            label: l.label,
            stroke: color,
            width: 1.6,
            dash: l.dash ? [5, 4] : undefined,
            fill: l.fill ? color + "33" : undefined,
            value: (_u: uPlot, v: number | null) => (v == null ? "—" : fmt(v)),
          };
        }),
      ],
      hooks:
        baseline === undefined
          ? {}
          : {
              drawClear: [
                (u: uPlot) => {
                  const y = u.valToPos(baseline, "y", true);
                  if (!Number.isFinite(y)) return;
                  const ctx = u.ctx;
                  ctx.save();
                  ctx.strokeStyle = axis;
                  ctx.globalAlpha = 0.5;
                  ctx.beginPath();
                  ctx.moveTo(u.bbox.left, y);
                  ctx.lineTo(u.bbox.left + u.bbox.width, y);
                  ctx.stroke();
                  ctx.restore();
                },
              ],
            },
    };
    const plot = new uPlot(opts, [x, ...lines.map((l) => l.values)] as uPlot.AlignedData, el);
    const observer = new ResizeObserver(() => plot.setSize({ width: el.clientWidth, height }));
    observer.observe(el);
    return () => {
      observer.disconnect();
      plot.destroy();
    };
  }, [x, lines, height, log, format, baseline]);

  if (x.length === 0) return <div className="empty">No data</div>;
  return <div ref={host} className="chart" />;
}
