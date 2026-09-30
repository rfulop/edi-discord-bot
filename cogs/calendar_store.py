import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from pathlib import Path


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    IF_NEEDED = "if_needed"


class SlotPeriod(StrEnum):
    AFTERNOON = "afternoon"
    LATE_AFTERNOON = "late_afternoon"
    EVENING = "evening"


PERIOD_LABELS = {
    SlotPeriod.AFTERNOON: "Après-midi",
    SlotPeriod.LATE_AFTERNOON: "Fin d'après-midi",
    SlotPeriod.EVENING: "Soir",
}

PERIOD_DEFAULT_TIMES = {
    SlotPeriod.AFTERNOON: ("14:00", "18:00"),
    SlotPeriod.LATE_AFTERNOON: ("18:00", "20:00"),
    SlotPeriod.EVENING: ("20:00", "23:59"),
}


@dataclass(frozen=True)
class Schedule:
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    role_id: int
    creator_id: int
    title: str
    status: str
    event_id: int | None
    reminders_enabled: bool
    next_reminder_at: datetime | None
    reminder_count: int


@dataclass(frozen=True)
class ScheduleSlot:
    id: int
    schedule_id: int
    day: date
    period: SlotPeriod

    @property
    def label(self) -> str:
        return f"{self.day.strftime('%d/%m/%Y')} — {PERIOD_LABELS[self.period]}"


@dataclass(frozen=True)
class SlotScore:
    slot: ScheduleSlot
    available: int
    if_needed: int

    @property
    def total(self) -> int:
        return self.available + self.if_needed


def generate_slot_specs(
    start_day: date, days: int, earliest_slot_start: datetime | None = None
):
    for offset in range(days):
        current_day = start_day + timedelta(days=offset)
        periods = [SlotPeriod.LATE_AFTERNOON, SlotPeriod.EVENING]
        if current_day.weekday() >= 5:
            periods.insert(0, SlotPeriod.AFTERNOON)
        for period in periods:
            start_time = time.fromisoformat(PERIOD_DEFAULT_TIMES[period][0])
            slot_start = datetime.combine(current_day, start_time)
            if earliest_slot_start and slot_start <= earliest_slot_start:
                continue
            yield current_day, period


class CalendarStore:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self):
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER,
                    role_id INTEGER NOT NULL,
                    creator_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    event_id INTEGER,
                    reminders_enabled INTEGER NOT NULL DEFAULT 1,
                    next_reminder_at TEXT,
                    reminder_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS schedule_slots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
                    day TEXT NOT NULL,
                    period TEXT NOT NULL,
                    UNIQUE(schedule_id, day, period)
                );

                CREATE TABLE IF NOT EXISTS availability (
                    schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
                    slot_id INTEGER NOT NULL REFERENCES schedule_slots(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('available', 'if_needed')),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(schedule_id, slot_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS schedule_responses (
                    schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(schedule_id, user_id)
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(schedules)")
            }
            if "reminders_enabled" not in columns:
                connection.execute(
                    "ALTER TABLE schedules ADD COLUMN reminders_enabled INTEGER NOT NULL DEFAULT 1"
                )
            if "next_reminder_at" not in columns:
                connection.execute(
                    "ALTER TABLE schedules ADD COLUMN next_reminder_at TEXT"
                )
                first_reminder_at = datetime.now(timezone.utc) + timedelta(hours=24)
                connection.execute(
                    """
                    UPDATE schedules SET next_reminder_at = ?
                    WHERE status = 'open' AND reminders_enabled = 1
                    """,
                    (first_reminder_at.isoformat(),),
                )
            if "reminder_count" not in columns:
                connection.execute(
                    "ALTER TABLE schedules ADD COLUMN reminder_count INTEGER NOT NULL DEFAULT 0"
                )

    def create_schedule(
        self,
        *,
        guild_id,
        channel_id,
        role_id,
        creator_id,
        title,
        start_day,
        days,
        earliest_slot_start=None,
        reminders_enabled=True,
    ) -> int:
        slot_specs = list(
            generate_slot_specs(start_day, days, earliest_slot_start)
        )
        if not slot_specs and earliest_slot_start:
            slot_specs = list(generate_slot_specs(start_day + timedelta(days=1), days))
        with self.connect() as connection:
            created_at = datetime.now(timezone.utc)
            next_reminder_at = (
                created_at + timedelta(hours=24) if reminders_enabled else None
            )
            cursor = connection.execute(
                """
                INSERT INTO schedules (
                    guild_id, channel_id, role_id, creator_id, title, created_at,
                    reminders_enabled, next_reminder_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    channel_id,
                    role_id,
                    creator_id,
                    title,
                    created_at.isoformat(),
                    int(reminders_enabled),
                    next_reminder_at.isoformat() if next_reminder_at else None,
                ),
            )
            schedule_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO schedule_slots (schedule_id, day, period) VALUES (?, ?, ?)",
                (
                    (schedule_id, slot_day.isoformat(), period.value)
                    for slot_day, period in slot_specs
                ),
            )
            return schedule_id

    def set_message_id(self, schedule_id: int, message_id: int):
        with self.connect() as connection:
            connection.execute(
                "UPDATE schedules SET message_id = ? WHERE id = ?",
                (message_id, schedule_id),
            )

    def get_schedule(self, schedule_id: int) -> Schedule | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
        return self._schedule_from_row(row) if row else None

    def get_active_schedules(self):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM schedules WHERE status = 'open' AND message_id IS NOT NULL"
            ).fetchall()
        return [self._schedule_from_row(row) for row in rows]

    def get_slots(self, schedule_id: int):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM schedule_slots WHERE schedule_id = ? ORDER BY day, id",
                (schedule_id,),
            ).fetchall()
        return [self._slot_from_row(row) for row in rows]

    def get_user_choices(self, schedule_id: int, user_id: int):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT slot_id, status FROM availability WHERE schedule_id = ? AND user_id = ?",
                (schedule_id, user_id),
            ).fetchall()
        return {row["slot_id"]: AvailabilityStatus(row["status"]) for row in rows}

    def save_user_choices(
        self,
        schedule_id: int,
        user_id: int,
        available_slot_ids,
        if_needed_slot_ids,
    ):
        available = {int(slot_id) for slot_id in available_slot_ids}
        if_needed = {int(slot_id) for slot_id in if_needed_slot_ids}
        if available & if_needed:
            raise ValueError("A slot cannot have two availability statuses")
        valid_slot_ids = {slot.id for slot in self.get_slots(schedule_id)}
        if not (available | if_needed) <= valid_slot_ids:
            raise ValueError("Unknown slot for this schedule")

        now = datetime.now().astimezone().isoformat()
        rows = [
            (schedule_id, slot_id, user_id, AvailabilityStatus.AVAILABLE.value, now)
            for slot_id in available
        ]
        rows.extend(
            (schedule_id, slot_id, user_id, AvailabilityStatus.IF_NEEDED.value, now)
            for slot_id in if_needed
        )
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM availability WHERE schedule_id = ? AND user_id = ?",
                (schedule_id, user_id),
            )
            connection.executemany(
                """
                INSERT INTO availability (schedule_id, slot_id, user_id, status, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.execute(
                """
                INSERT INTO schedule_responses (schedule_id, user_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(schedule_id, user_id)
                DO UPDATE SET updated_at = excluded.updated_at
                """,
                (schedule_id, user_id, now),
            )

    def get_respondent_ids(self, schedule_id: int):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT user_id FROM schedule_responses WHERE schedule_id = ?",
                (schedule_id,),
            ).fetchall()
        return {row["user_id"] for row in rows}

    def get_slot_responses(self, schedule_id: int):
        responses = {
            slot.id: {
                AvailabilityStatus.AVAILABLE: set(),
                AvailabilityStatus.IF_NEEDED: set(),
            }
            for slot in self.get_slots(schedule_id)
        }
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT slot_id, user_id, status
                FROM availability
                WHERE schedule_id = ?
                """,
                (schedule_id,),
            ).fetchall()
        for row in rows:
            responses[row["slot_id"]][AvailabilityStatus(row["status"])].add(
                row["user_id"]
            )
        return responses

    def get_scores(self, schedule_id: int):
        slots = {slot.id: slot for slot in self.get_slots(schedule_id)}
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT slot_id,
                    SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available,
                    SUM(CASE WHEN status = 'if_needed' THEN 1 ELSE 0 END) AS if_needed
                FROM availability
                WHERE schedule_id = ?
                GROUP BY slot_id
                """,
                (schedule_id,),
            ).fetchall()
        counts = {
            row["slot_id"]: (row["available"], row["if_needed"]) for row in rows
        }
        scores = [
            SlotScore(slot, *counts.get(slot.id, (0, 0))) for slot in slots.values()
        ]
        return sorted(
            scores,
            key=lambda score: (score.total, score.available, -score.slot.id),
            reverse=True,
        )

    def finalize(self, schedule_id: int, event_id: int):
        with self.connect() as connection:
            connection.execute(
                "UPDATE schedules SET status = 'closed', event_id = ? WHERE id = ?",
                (event_id, schedule_id),
            )

    def try_begin_finalization(self, schedule_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE schedules SET status = 'finalizing' WHERE id = ? AND status = 'open'",
                (schedule_id,),
            )
        return cursor.rowcount == 1

    def cancel_finalization(self, schedule_id: int):
        with self.connect() as connection:
            connection.execute(
                "UPDATE schedules SET status = 'open' WHERE id = ? AND status = 'finalizing'",
                (schedule_id,),
            )

    def recover_interrupted_finalizations(self):
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE schedules SET status = 'open' WHERE status = 'finalizing'"
            )
        return cursor.rowcount

    def get_due_reminders(self, now=None):
        now = now or datetime.now(timezone.utc)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM schedules
                WHERE status = 'open'
                    AND reminders_enabled = 1
                    AND next_reminder_at IS NOT NULL
                    AND next_reminder_at <= ?
                """,
                (now.astimezone(timezone.utc).isoformat(),),
            ).fetchall()
        return [self._schedule_from_row(row) for row in rows]

    def schedule_next_reminder(self, schedule_id: int, now=None):
        now = now or datetime.now(timezone.utc)
        next_reminder_at = now.astimezone(timezone.utc) + timedelta(hours=24)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE schedules SET next_reminder_at = ?
                WHERE id = ? AND status = 'open' AND reminders_enabled = 1
                """,
                (next_reminder_at.isoformat(), schedule_id),
            )

    def mark_reminder_sent(self, schedule_id: int, now=None):
        now = now or datetime.now(timezone.utc)
        next_reminder_at = now.astimezone(timezone.utc) + timedelta(hours=24)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE schedules
                SET next_reminder_at = ?, reminder_count = reminder_count + 1
                WHERE id = ? AND status = 'open' AND reminders_enabled = 1
                """,
                (next_reminder_at.isoformat(), schedule_id),
            )

    def disable_reminders(self, schedule_id: int):
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE schedules
                SET reminders_enabled = 0, next_reminder_at = NULL
                WHERE id = ?
                """,
                (schedule_id,),
            )

    @staticmethod
    def _schedule_from_row(row):
        return Schedule(
            id=row["id"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            message_id=row["message_id"],
            role_id=row["role_id"],
            creator_id=row["creator_id"],
            title=row["title"],
            status=row["status"],
            event_id=row["event_id"],
            reminders_enabled=bool(row["reminders_enabled"]),
            next_reminder_at=(
                datetime.fromisoformat(row["next_reminder_at"])
                if row["next_reminder_at"]
                else None
            ),
            reminder_count=row["reminder_count"],
        )

    @staticmethod
    def _slot_from_row(row):
        return ScheduleSlot(
            id=row["id"],
            schedule_id=row["schedule_id"],
            day=date.fromisoformat(row["day"]),
            period=SlotPeriod(row["period"]),
        )
