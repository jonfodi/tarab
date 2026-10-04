-- One row per person who joins the waitlist on tarab.top.
CREATE TABLE waitlist (
  email      TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  country    TEXT
);
