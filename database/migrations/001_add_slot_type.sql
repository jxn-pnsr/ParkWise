-- Phase 1 migration: EV charging bays.
-- Only for a database created from the team schema before slot_type was added. A fresh install from schema.sql
-- already has this column. Run once:
--   mysql -u root -p parkwise < database/migrations/001_add_slot_type.sql

ALTER TABLE Parking_Slots
  ADD COLUMN slot_type ENUM('Standard','EV') NOT NULL DEFAULT 'Standard' AFTER slot_code;

UPDATE Parking_Slots SET slot_type = 'EV' WHERE slot_code IN ('A1', 'B1') AND is_deleted = 0;
