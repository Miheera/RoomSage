import Icon from "../Icon";

export default function PhotoGrid({ photos, onRemove }) {
  if (!photos.length) return null;
  return (
    <ul className="photos">
      {photos.map((p, i) => (
        <li key={p.id} className={p.warnings.length ? "photos__item has-warning" : "photos__item"}>
          <img src={p.url} alt={`Room photo ${i + 1}`} loading="lazy" />
          <button className="photos__remove" onClick={() => onRemove(p.id)} aria-label={`Remove photo ${i + 1}`}>
            <Icon name="x" size={16} />
          </button>
          {p.warnings.length > 0 && <span className="photos__warn">{p.warnings[0]}</span>}
        </li>
      ))}
    </ul>
  );
}
