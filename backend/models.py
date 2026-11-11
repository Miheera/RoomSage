import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, Numeric, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    projects: Mapped[list["Project"]] = relationship(back_populates="owner")
    usage: Mapped["Usage | None"] = relationship(back_populates="user", uselist=False)


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskType(str, enum.Enum):
    COLMAP_POSE_ESTIMATION = "COLMAP_POSE_ESTIMATION"
    GAUSSIAN_SPLATTING_TRAIN = "GAUSSIAN_SPLATTING_TRAIN"
    SAM_3D_SEGMENTATION = "SAM_3D_SEGMENTATION"
    INSTRUCT_GS2GS_EDIT = "INSTRUCT_GS2GS_EDIT"
    REDESIGN_2D = "REDESIGN_2D"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    room_type: Mapped[str | None] = mapped_column(String(80))
    photo_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    depth_map_s3_key: Mapped[str | None] = mapped_column(String(1024))
    s3_redesign_preview_key: Mapped[str | None] = mapped_column(String(1024))
    scene_s3_key: Mapped[str | None] = mapped_column(String(1024))
    shopping_list_s3_key: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    jobs: Mapped[list["Job"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    owner: Mapped["User | None"] = relationship(back_populates="projects")
    rooms: Mapped[list["Room"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT", server_default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    project: Mapped["Project"] = relationship(back_populates="rooms")
    photos: Mapped[list["RoomPhoto"]] = relationship(back_populates="room", cascade="all, delete-orphan", order_by="RoomPhoto.order")
    designs: Mapped[list["Design"]] = relationship(back_populates="room", cascade="all, delete-orphan")


class RoomPhoto(Base):
    __tablename__ = "room_photos"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    angle_label: Mapped[str | None] = mapped_column(String(80))
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    room: Mapped["Room"] = relationship(back_populates="photos")


class Design(Base):
    __tablename__ = "designs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("designs.id", ondelete="SET NULL"), index=True)
    style: Mapped[str] = mapped_column(String(120), nullable=False)
    palette: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_url: Mapped[str | None] = mapped_column(String(1024))
    structure_map_url: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="QUEUED", index=True)
    starred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    image_provider: Mapped[str] = mapped_column(
        String(40), nullable=False, default="unknown", server_default="unknown"
    )
    model_ids: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    render_metadata: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    image_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    room: Mapped["Room"] = relationship(back_populates="designs")
    parent: Mapped["Design | None"] = relationship(remote_side="Design.id", back_populates="children")
    children: Mapped[list["Design"]] = relationship(back_populates="parent")
    items: Mapped[list["DesignItem"]] = relationship(back_populates="design", cascade="all, delete-orphan")


class DesignItem(Base):
    __tablename__ = "design_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    design_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("designs.id", ondelete="CASCADE"), nullable=False, index=True)
    catalog_item_id: Mapped[str] = mapped_column(ForeignKey("catalog_products.product_id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(80), nullable=False, default="decor", server_default="decor")
    x: Mapped[float | None] = mapped_column(Numeric(7, 5))
    y: Mapped[float | None] = mapped_column(Numeric(7, 5))
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_essential: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    visibility_reason: Mapped[str | None] = mapped_column(Text)
    design: Mapped["Design"] = relationship(back_populates="items")


class Usage(Base):
    __tablename__ = "usage"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    generations_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    jobs_3d_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    user: Mapped["User"] = relationship(back_populates="usage")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    room_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True)
    design_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("designs.id", ondelete="SET NULL"), index=True)
    task_type: Mapped[TaskType] = mapped_column(
        Enum(
            TaskType,
            name="task_type",
            native_enum=False,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            name="job_status",
            native_enum=False,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
        default=JobStatus.QUEUED,
        index=True,
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    output_key: Mapped[str | None] = mapped_column(String(1024))
    depth_map_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project: Mapped[Project] = relationship(back_populates="jobs")


class CatalogProduct(Base):
    __tablename__ = "catalog_products"

    product_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="object", server_default="object", index=True)
    selectable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)
    image_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    theme: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    room_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    price_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    dimensions_m: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    primary_color: Mapped[str] = mapped_column(String(160), nullable=False)
    primary_material: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_phrase: Mapped[str] = mapped_column(Text, nullable=False)
    image_s3_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    buy_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_image_s3_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
