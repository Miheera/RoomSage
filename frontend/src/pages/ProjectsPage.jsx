import { Link } from "react-router-dom";
import { useProjects, projectsStore } from "../lib/storage";
import { formatDate, roomLabel } from "../lib/format";
import Icon from "../components/Icon";

export default function ProjectsPage() {
  const projects = useProjects();

  return (
    <main className="page">
      <div className="page__head">
        <h1>Your rooms</h1>
        <Link to="/new" className="btn btn--primary">
          <Icon name="plus" /> Scan a room
        </Link>
      </div>

      {projects.length === 0 ? (
        <section className="empty">
          <h2>Redesign the room you actually have</h2>
          <p>
            Take a few photos while walking around a room. RoomSage rebuilds it in 3D, shows it redesigned in your style and budget, and lists exactly what to buy.
          </p>
          <Link to="/new" className="btn btn--primary btn--lg">
            Scan your first room
          </Link>
        </section>
      ) : (
        <ul className="rooms">
          {projects.map((p) => (
            <li key={p.id} className="room">
              <Link to={`/project/${p.id}`} className="room__link">
                {p.cover ? <img src={p.cover} alt="" loading="lazy" /> : <div className="room__ph" />}
                <div className="room__body">
                  <h2>{p.name}</h2>
                  <p>
                    {roomLabel(p.roomType)}, {p.photoCount} photos, {formatDate(p.createdAt)}
                  </p>
                  {p.edits?.length > 0 && <p className="room__tag">{p.edits.length} redesign{p.edits.length > 1 ? "s" : ""}</p>}
                </div>
              </Link>
              <button
                className="room__del"
                aria-label={`Remove ${p.name} from this device`}
                title="Remove from this device"
                onClick={() => window.confirm(`Remove "${p.name}" from this device? The room stays on the server.`) && projectsStore.remove(p.id)}
              >
                <Icon name="trash" size={18} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
