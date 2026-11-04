import { useState } from "react";
import { formatINR } from "../../lib/format";
import { budgetStatus, totals } from "../../lib/budget";
import Icon from "../Icon";

function Price({ price, qty }) {
  const q = qty > 1 ? ` x ${qty}` : "";
  if (price.kind === "catalog") {
    return (
      <span className="price price--catalog" title="Real price from the product catalog">
        <Icon name="check" size={14} /> {formatINR(price.amount)}
        {q}
      </span>
    );
  }
  return (
    <span className="price price--estimate" title="No catalog match. This is an estimated range.">
      about {formatINR(price.min)} to {formatINR(price.max)}
      {q}
    </span>
  );
}

function BudgetBar({ t, cap }) {
  const scale = Math.max(cap || 0, t.high) * 1.05 || 1;
  const status = budgetStatus(t, cap);
  return (
    <div className="budget">
      <div className="budget__nums">
        <strong>
          {t.estMax ? `${formatINR(t.low)} to ${formatINR(t.high)}` : formatINR(t.low)}
        </strong>
        {cap ? <span>of {formatINR(cap)} budget</span> : <span>no budget set</span>}
      </div>
      <div className="budget__track" aria-hidden="true">
        <div className="budget__catalog" style={{ width: `${(t.catalog / scale) * 100}%` }} />
        <div className="budget__est" style={{ left: `${(t.catalog / scale) * 100}%`, width: `${((t.estMax) / scale) * 100}%` }} />
        {cap ? <div className="budget__cap" style={{ left: `${(cap / scale) * 100}%` }} /> : null}
      </div>
      <p className="budget__legend">
        <span className="dot dot--catalog" /> Catalog prices <span className="dot dot--est" /> Estimates
      </p>
      {status && <p className={`budget__status budget__status--${status.tone}`}>{status.text}</p>}
    </div>
  );
}

export default function PlanPanel({ plan, isSample, onUseSample, budgetCap, onBudgetCap, onReplan, replanning, checked, onToggleStep, onPlace, placing, canPlace, error }) {
  const [capDraft, setCapDraft] = useState(budgetCap ?? "");

  if (plan === undefined) return <p className="muted">Loading your plan…</p>;

  if (plan === null) {
    return (
      <div className="empty-panel">
        <h3>Your plan isn't ready yet</h3>
        <p>The plan lists what to move, what to paint and what to buy, with real prices where we have them. It appears here once the planner has run for this room.</p>
        {error && <p className="notice notice--error">{error.message}</p>}
        {import.meta.env.DEV && (
          <button className="btn btn--ghost" onClick={onUseSample}>
            Preview with a sample plan
          </button>
        )}
      </div>
    );
  }

  const t = totals(plan.shopping);
  const cap = budgetCap ?? plan.budget?.cap ?? null;

  return (
    <div className="plan">
      {isSample && <p className="notice">This is a sample plan for layout testing, not generated for your room.</p>}
      <p className="plan__summary">{plan.summary}</p>

      <section>
        <h3>Colours</h3>
        <ul className="palette">
          {plan.palette.map((c) => (
            <li key={c.hex + c.name}>
              <span className="swatch" style={{ background: c.hex }} />
              <span>
                <strong>{c.name}</strong>
                <small>{c.role}</small>
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3>Do this, in order</h3>
        <ol className="steps">
          {plan.steps.map((s) => (
            <li key={s.id} className={checked?.[s.id] ? "is-done" : ""}>
              <label>
                <input type="checkbox" checked={!!checked?.[s.id]} onChange={() => onToggleStep(s.id)} />
                <span>
                  <strong>{s.title}</strong>
                  {s.detail && <small>{s.detail}</small>}
                </span>
              </label>
            </li>
          ))}
        </ol>
      </section>

      <section>
        <h3>Shopping list</h3>
        <BudgetBar t={t} cap={cap} />
        <form
          className="capform"
          onSubmit={(e) => {
            e.preventDefault();
            const n = Number(capDraft);
            if (n > 0) {
              onBudgetCap(n);
              onReplan(n);
            }
          }}
        >
          <label>
            Budget (₹)
            <input inputMode="numeric" value={capDraft} onChange={(e) => setCapDraft(e.target.value.replace(/[^\d]/g, ""))} placeholder="e.g. 30000" />
          </label>
          <button className="btn btn--ghost" disabled={!Number(capDraft) || replanning}>
            {replanning ? "Re-planning…" : "Re-plan within budget"}
          </button>
        </form>

        <ul className="items">
          {plan.shopping.map((it) => (
            <li key={it.id} className="item">
              <div className="item__main">
                <strong>{it.name}</strong>
                <small>{it.category}</small>
                <Price price={it.price} qty={it.qty} />
              </div>
              <div className="item__actions">
                {it.url && (
                  <a className="btn btn--ghost btn--sm" href={it.url} target="_blank" rel="noreferrer">
                    <Icon name="link" size={16} /> Buy
                  </a>
                )}
                {it.catalog_image_key && (
                  <button className="btn btn--ghost btn--sm" onClick={() => onPlace(it)} disabled={!canPlace || placing}
                    title="Aim the 3D view at the spot on the floor, then press this">
                    {placing === it.id ? "Placing…" : "Place in room"}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
        {plan.shopping.some((i) => i.catalog_image_key) && (
          <p className="muted">To place a piece, point the 3D view at the floor spot where it should stand, then press "Place in room".</p>
        )}
      </section>
    </div>
  );
}
