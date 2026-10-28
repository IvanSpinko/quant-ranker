// Small presentational pieces shared by the screens.
import type { ReactNode } from "react";

export function Metric({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: string }) {
  return (
    <div className="metric" title={hint}>
      <div className="metric-label">
        {label}
        {hint && <span className="hint-dot">?</span>}
      </div>
      <div className={`metric-value ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

export function Card({ title, aside, children, className }: { title?: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || aside) && (
        <header className="card-head">
          {title && <h3>{title}</h3>}
          {aside}
        </header>
      )}
      {children}
    </section>
  );
}

/** One horizontal bar per row, centred on zero, for features and SHAP contributions. */
export function Bars({ rows, format, max }: { rows: { label: string; value: number }[]; format: (v: number) => string; max?: number }) {
  const scale = max ?? Math.max(1e-12, ...rows.map((r) => Math.abs(r.value)));
  return (
    <div className="bars">
      {rows.map((r) => {
        const width = Math.min(50, (Math.abs(r.value) / scale) * 50);
        return (
          <div className="bar-row" key={r.label}>
            <span className="bar-label">{r.label}</span>
            <span className="bar-track">
              <span className={`bar ${r.value >= 0 ? "pos" : "neg"}`} style={r.value >= 0 ? { left: "50%", width: `${width}%` } : { right: "50%", width: `${width}%` }} />
              <span className="bar-axis" />
            </span>
            <span className={`bar-value ${r.value >= 0 ? "pos" : "neg"}`}>{format(r.value)}</span>
          </div>
        );
      })}
    </div>
  );
}

export function Explain({ children }: { children: ReactNode }) {
  return <p className="explain">{children}</p>;
}

export function ErrorBox({ error }: { error: string }) {
  return <div className="error">{error}</div>;
}
