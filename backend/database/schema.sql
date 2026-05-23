-- SQLite schema for research-jarvis.
-- All tables use CREATE TABLE IF NOT EXISTS so this file is safe to run on every boot.
-- PRAGMA foreign_keys = ON is set in db.py (connection setup), not here.
-- INTEGER is used for booleans (SQLite has no native bool): 0 = false, 1 = true.
-- All timestamps are TEXT in ISO 8601 format via datetime('now').

-- Root entity. Every conversation, message, and memory row belongs to a project.
-- Business rule: exactly one row has is_active = 1 at all times.
-- This is enforced in Python (sqlite_store.py), not in SQL — SQLite cannot CHECK
-- across multiple rows natively.
CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    is_active  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- A conversation groups a sequence of messages from one session.
-- One project can have many conversations (one per session by default).
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Individual user and assistant turns within a conversation.
-- project_id is denormalized here (derivable via conversation_id) so that
-- project-scoped queries like "show me all messages in project X" need no JOIN.
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    role            TEXT    NOT NULL,   -- 'user' or 'assistant'
    content         TEXT    NOT NULL,
    provider        TEXT,               -- 'gemini', 'groq', NULL for user messages
    model           TEXT,               -- e.g. 'gemini-2.5-flash', NULL for user messages
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Persistent memory items extracted from conversations.
-- importance is a 1.0–10.0 score assigned by an LLM on Day 6.
-- chroma_id links to the matching ChromaDB vector entry (populated Day 6; NULL today).
CREATE TABLE IF NOT EXISTS memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    content    TEXT    NOT NULL,
    importance REAL    NOT NULL DEFAULT 5.0,
    source     TEXT,               -- 'user', 'assistant', or 'log_to_project'
    chroma_id  TEXT,               -- NULL until Day 6 wires ChromaDB
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Tasks and reminders. Not wired to anything until Day 26 (timers/notifications).
-- Schema is created now so Day 26 needs no migration.
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    description TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',  -- 'pending', 'done', 'cancelled'
    due_at      TEXT,               -- ISO 8601, nullable
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- One row per LLM API call. No foreign keys — cost records should survive
-- even if conversations or projects are deleted.
CREATE TABLE IF NOT EXISTS cost_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    provider          TEXT    NOT NULL,   -- 'gemini', 'groq', 'openai'
    model             TEXT    NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_usd     REAL    NOT NULL DEFAULT 0.0,
    request_id        TEXT,               -- links to HTTP X-Request-ID; NULL for non-HTTP calls
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);
