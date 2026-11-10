SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE DATABASE IF NOT EXISTS parkwise
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;
USE parkwise;

DROP VIEW  IF EXISTS v_lot_availability;
DROP VIEW  IF EXISTS v_slot_current_status;
DROP VIEW  IF EXISTS v_reservation_details;
DROP TABLE IF EXISTS Integration_Outbox;
DROP TABLE IF EXISTS Payments;
DROP TABLE IF EXISTS Reservations;
DROP TABLE IF EXISTS Parking_Slots;
DROP TABLE IF EXISTS Parking_Lots;
DROP TABLE IF EXISTS Customer_Vehicles;
DROP TABLE IF EXISTS System_Config;
DROP TABLE IF EXISTS Users;

SET FOREIGN_KEY_CHECKS = 1;


# Users
CREATE TABLE Users (
  user_id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
  email              VARCHAR(255) NOT NULL,
  password_hash      VARCHAR(255) NOT NULL,
  full_name          VARCHAR(150) NOT NULL,
  phone              VARCHAR(20)  NULL,
  role               ENUM('Customer','Staff','Admin') NOT NULL DEFAULT 'Customer',
  is_active          TINYINT(1)   NOT NULL DEFAULT 1,      -- soft delete = 0
  failed_login_count TINYINT UNSIGNED NOT NULL DEFAULT 0,
  locked_until       DATETIME     NULL,
  created_at         DATETIME     NOT NULL DEFAULT (UTC_TIMESTAMP()),
  updated_at         DATETIME     NULL,
  PRIMARY KEY (user_id),
  UNIQUE KEY uq_users_email (email),
  KEY idx_users_role_active (role, is_active),
  CONSTRAINT chk_users_email CHECK (email LIKE '%_@_%.__%')
) ENGINE=InnoDB;


# System_Config (rates and rules that the admin can change)
CREATE TABLE System_Config (
  config_key   VARCHAR(60)  NOT NULL,
  config_value VARCHAR(100) NOT NULL,
  description  VARCHAR(255) NULL,
  updated_at   DATETIME     NOT NULL DEFAULT (UTC_TIMESTAMP()),
  updated_by   INT UNSIGNED NULL,
  PRIMARY KEY (config_key),
  CONSTRAINT fk_config_user FOREIGN KEY (updated_by) REFERENCES Users(user_id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB;


# Customer_Vehicles
CREATE TABLE Customer_Vehicles (
  vehicle_id   INT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id      INT UNSIGNED NOT NULL,
  plate_number VARCHAR(20)  NOT NULL,
  province     VARCHAR(60)  NOT NULL,
  is_default   TINYINT(1)   NOT NULL DEFAULT 0,
  is_deleted   TINYINT(1)   NOT NULL DEFAULT 0, 
  created_at   DATETIME     NOT NULL DEFAULT (UTC_TIMESTAMP()),
  updated_at   DATETIME     NULL,
  active_plate_key  VARCHAR(100)
    GENERATED ALWAYS AS (IF(is_deleted = 0, CONCAT(plate_number, '|', province), NULL)) VIRTUAL,
  default_owner_key INT UNSIGNED
    GENERATED ALWAYS AS (IF(is_default = 1 AND is_deleted = 0, user_id, NULL)) VIRTUAL,
  PRIMARY KEY (vehicle_id),
  UNIQUE KEY uq_vehicle_active_plate (active_plate_key),
  UNIQUE KEY uq_vehicle_one_default (default_owner_key), 
  KEY idx_vehicle_user (user_id, is_deleted),
  KEY idx_vehicle_plate (plate_number),
  CONSTRAINT fk_vehicle_user FOREIGN KEY (user_id) REFERENCES Users(user_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT chk_vehicle_plate CHECK (CHAR_LENGTH(TRIM(plate_number)) >= 2)
) ENGINE=InnoDB;


# Parking_Lots
CREATE TABLE Parking_Lots (
  lot_id        INT UNSIGNED NOT NULL AUTO_INCREMENT,
  lot_name      VARCHAR(100) NOT NULL,
  location_desc VARCHAR(255) NULL,
  is_active     TINYINT(1)   NOT NULL DEFAULT 1,
  created_at    DATETIME     NOT NULL DEFAULT (UTC_TIMESTAMP()),
  updated_at    DATETIME     NULL,
  PRIMARY KEY (lot_id),
  UNIQUE KEY uq_lot_name (lot_name)
) ENGINE=InnoDB;


# Parking_Slots
# slot_status only stores Active / Maintenance. Available, Held, Reserved and
# Occupied are worked out from Reservations for the time range the user picks.
CREATE TABLE Parking_Slots (
  slot_id     INT UNSIGNED NOT NULL AUTO_INCREMENT,
  lot_id      INT UNSIGNED NOT NULL,
  slot_code   VARCHAR(20)  NOT NULL,
  slot_type   ENUM('Standard','EV') NOT NULL DEFAULT 'Standard',  -- EV = has a charger (phase 1)
  slot_status ENUM('Active','Maintenance') NOT NULL DEFAULT 'Active',
  is_deleted  TINYINT(1)   NOT NULL DEFAULT 0,
  created_at  DATETIME     NOT NULL DEFAULT (UTC_TIMESTAMP()),
  updated_at  DATETIME     NULL,
  active_code_key VARCHAR(40)
    GENERATED ALWAYS AS (IF(is_deleted = 0, CONCAT(lot_id, '|', slot_code), NULL)) VIRTUAL,
  PRIMARY KEY (slot_id),
  UNIQUE KEY uq_slot_active_code (active_code_key),    
  KEY idx_slot_lot (lot_id, slot_status, is_deleted),
  CONSTRAINT fk_slot_lot FOREIGN KEY (lot_id) REFERENCES Parking_Lots(lot_id)
    ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB;


# Reservations
CREATE TABLE Reservations (
  reservation_id    INT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id           INT UNSIGNED NOT NULL,
  vehicle_id        INT UNSIGNED NOT NULL,
  slot_id           INT UNSIGNED NOT NULL,
  booking_type      ENUM('Advance','Instant') NOT NULL,
  start_time        DATETIME     NOT NULL,
  end_time          DATETIME     NOT NULL, 
  duration_hours    TINYINT UNSIGNED NOT NULL,
  status            ENUM('Held','Confirmed','In Use','Completed','Cancelled','Expired','No-show')
                    NOT NULL DEFAULT 'Held',
  hold_expires_at   DATETIME     NULL,
  confirmed_at      DATETIME     NULL,
  checked_in_at     DATETIME     NULL,
  checked_out_at    DATETIME     NULL,
  checked_in_by     INT UNSIGNED NULL,
  checked_out_by    INT UNSIGNED NULL,
  cancelled_at      DATETIME     NULL,
  cancelled_by      INT UNSIGNED NULL,
  cancel_reason     VARCHAR(255) NULL,
  is_overstay       TINYINT(1)   NOT NULL DEFAULT 0,
  end_warning_sent  TINYINT(1)   NOT NULL DEFAULT 0, 
  overstay_notified TINYINT(1)   NOT NULL DEFAULT 0,    
  created_at        DATETIME     NOT NULL DEFAULT (UTC_TIMESTAMP()),
  updated_at        DATETIME     NULL,
  PRIMARY KEY (reservation_id),
  UNIQUE KEY uq_res_id_user (reservation_id, user_id),  

  KEY idx_res_slot_time    (slot_id, start_time, end_time),
  KEY idx_res_slot_status  (slot_id, status),
  KEY idx_res_vehicle_time (vehicle_id, start_time, end_time),
  KEY idx_res_user_status  (user_id, status),
  KEY idx_res_hold_expiry  (status, hold_expires_at),  
  KEY idx_res_noshow       (status, start_time),    
  KEY idx_res_endtime      (status, end_time), 

  CONSTRAINT fk_res_user      FOREIGN KEY (user_id)        REFERENCES Users(user_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_res_vehicle   FOREIGN KEY (vehicle_id)     REFERENCES Customer_Vehicles(vehicle_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_res_slot      FOREIGN KEY (slot_id)        REFERENCES Parking_Slots(slot_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_res_in_by     FOREIGN KEY (checked_in_by)  REFERENCES Users(user_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_res_out_by    FOREIGN KEY (checked_out_by) REFERENCES Users(user_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_res_cancel_by FOREIGN KEY (cancelled_by)   REFERENCES Users(user_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,

  CONSTRAINT chk_res_time      CHECK (end_time > start_time),
  CONSTRAINT chk_res_duration  CHECK (duration_hours >= 1
                                      AND TIMESTAMPDIFF(MINUTE, start_time, end_time) = duration_hours * 60),
  CONSTRAINT chk_res_hold      CHECK (status <> 'Held' OR hold_expires_at IS NOT NULL),
  CONSTRAINT chk_res_confirmed CHECK (status NOT IN ('Confirmed','In Use','Completed','No-show')
                                      OR confirmed_at IS NOT NULL),
  CONSTRAINT chk_res_checkin   CHECK (status NOT IN ('In Use','Completed') OR checked_in_at IS NOT NULL),
  CONSTRAINT chk_res_completed CHECK (status <> 'Completed'
                                      OR (checked_out_at IS NOT NULL AND checked_out_at >= checked_in_at)),
  CONSTRAINT chk_res_cancelled CHECK (status <> 'Cancelled' OR cancelled_at IS NOT NULL)
) ENGINE=InnoDB;


# Payments (simulated, no real payment gateway)
CREATE TABLE Payments (
  payment_id     INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  reservation_id INT UNSIGNED  NOT NULL,
  user_id        INT UNSIGNED  NOT NULL,
  base_fee       DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  overstay_fee   DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  total_amount   DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  method         ENUM('Cash','QR') NULL,                   -- NULL while Pending
  status         ENUM('Pending','Paid','Failed') NOT NULL DEFAULT 'Pending',
  paid_at        DATETIME      NULL,
  is_deleted     TINYINT(1)    NOT NULL DEFAULT 0,         -- soft delete
  created_at     DATETIME      NOT NULL DEFAULT (UTC_TIMESTAMP()),
  updated_at     DATETIME      NULL,
  PRIMARY KEY (payment_id),
  UNIQUE KEY uq_payment_reservation (reservation_id),      -- one payment per reservation
  KEY idx_payment_user (user_id, status),
  KEY idx_payment_status (status),
  -- makes sure payment.user_id is the same user who owns the reservation
  CONSTRAINT fk_payment_res_user FOREIGN KEY (reservation_id, user_id)
    REFERENCES Reservations (reservation_id, user_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_payment_user FOREIGN KEY (user_id) REFERENCES Users(user_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT chk_payment_amount CHECK (base_fee >= 0 AND overstay_fee >= 0
                                       AND total_amount = base_fee + overstay_fee),
  CONSTRAINT chk_payment_paid   CHECK (status <> 'Paid' OR (paid_at IS NOT NULL AND method IS NOT NULL))
) ENGINE=InnoDB;


CREATE TABLE Integration_Outbox (
  event_id     CHAR(36)     NOT NULL,                      -- UUID made by the backend
  seq          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,    -- keeps events in order
  event_type   VARCHAR(60)  NOT NULL,                      -- e.g. reservation.confirmed
  aggregate_id VARCHAR(40)  NULL,                          -- e.g. the reservation_id
  payload      JSON         NOT NULL,
  status       ENUM('Pending','Sent','Failed') NOT NULL DEFAULT 'Pending',
  retry_count  TINYINT UNSIGNED NOT NULL DEFAULT 0,
  last_error   VARCHAR(500) NULL,
  created_at   DATETIME     NOT NULL DEFAULT (UTC_TIMESTAMP()),
  sent_at      DATETIME     NULL,
  PRIMARY KEY (event_id),
  UNIQUE KEY uq_outbox_seq (seq),
  KEY idx_outbox_poll (status, seq),
  KEY idx_outbox_type (event_type),
  KEY idx_outbox_aggregate (aggregate_id),
  CONSTRAINT chk_outbox_sent CHECK (status <> 'Sent' OR sent_at IS NOT NULL)
) ENGINE=InnoDB;


-- Default config values
INSERT INTO System_Config (config_key, config_value, description) VALUES
 ('HOLD_MINUTES',                     '5',  'Minutes a slot is held while waiting for confirmation'),
 ('NOSHOW_GRACE_MINUTES',             '30', 'Minutes after start time before a reservation becomes No-show'),
 ('EARLY_CHECKIN_MINUTES',            '15', 'How early a customer can check in before the start time'),
 ('ADVANCE_CANCEL_HOURS',             '1',  'Advance booking: cancel at least this many hours before start'),
 ('INSTANT_CANCEL_MINUTES',           '10', 'Instant booking: minutes after confirmation the customer can still cancel'),
 ('MIN_DURATION_HOURS',               '1',  'Minimum booking duration (hours)'),
 ('MAX_DURATION_HOURS',               '12', 'Maximum booking duration (hours)'),
 ('MAX_ADVANCE_DAYS',                 '7',  'How many days ahead a customer can book'),
 ('MAX_ACTIVE_RESERVATIONS_PER_USER', '3',  'Max active reservations per customer'),
 ('RATE_PER_HOUR',                    '20', 'Normal parking rate (baht per hour)'),
 ('OVERSTAY_GRACE_MINUTES',           '15', 'Grace period after end time before overstay fee starts'),
 ('OVERSTAY_TIER1_RATE',              '40', 'Overstay rate for the 1st extra hour (baht per hour)'),
 ('OVERSTAY_TIER2_RATE',              '60', 'Overstay rate from the 2nd extra hour (baht per hour)'),
 ('OVERSTAY_WARN_MINUTES',            '15', 'Send "ending soon" notification this many minutes before end time'),
 ('LOGIN_MAX_FAILED',                 '5',  'Failed logins before the account is locked'),
 ('LOGIN_LOCK_MINUTES',               '5',  'Minutes the account stays locked');

-- Views
-- reservation + user + vehicle + slot + lot + payment (good for the JOIN screenshot)
CREATE VIEW v_reservation_details AS
SELECT r.reservation_id, r.status, r.booking_type,
       r.start_time, r.end_time, r.duration_hours,
       r.checked_in_at, r.checked_out_at, r.is_overstay,
       u.user_id, u.full_name AS customer_name, u.email,
       v.vehicle_id, v.plate_number, v.province,
       s.slot_id, s.slot_code,
       l.lot_id, l.lot_name,
       p.payment_id, p.total_amount, p.status AS payment_status
FROM Reservations r
JOIN Users u             ON u.user_id    = r.user_id
JOIN Customer_Vehicles v ON v.vehicle_id = r.vehicle_id
JOIN Parking_Slots s     ON s.slot_id    = r.slot_id
JOIN Parking_Lots l      ON l.lot_id     = s.lot_id
LEFT JOIN Payments p     ON p.reservation_id = r.reservation_id AND p.is_deleted = 0;

-- status of each slot right now
-- priority: Maintenance > Occupied > Reserved > Held > Available
CREATE VIEW v_slot_current_status AS
SELECT s.slot_id, s.lot_id, s.slot_code, s.slot_status,
  CASE
    WHEN s.slot_status = 'Maintenance' THEN 'Maintenance'
    WHEN EXISTS (SELECT 1 FROM Reservations r
                 WHERE r.slot_id = s.slot_id AND r.status = 'In Use') THEN 'Occupied'
    WHEN EXISTS (SELECT 1 FROM Reservations r
                 WHERE r.slot_id = s.slot_id AND r.status = 'Confirmed'
                   AND r.start_time <= UTC_TIMESTAMP() AND r.end_time > UTC_TIMESTAMP()) THEN 'Reserved'
    WHEN EXISTS (SELECT 1 FROM Reservations r
                 WHERE r.slot_id = s.slot_id AND r.status = 'Held'
                   AND r.hold_expires_at > UTC_TIMESTAMP()
                   AND r.start_time <= UTC_TIMESTAMP() AND r.end_time > UTC_TIMESTAMP()) THEN 'Held'
    ELSE 'Available'
  END AS display_status
FROM Parking_Slots s
WHERE s.is_deleted = 0;

-- free / total slots per lot (right now)
CREATE VIEW v_lot_availability AS
SELECT l.lot_id, l.lot_name, l.is_active,
       COUNT(c.slot_id)                                   AS total_slots,
       COALESCE(SUM(c.display_status = 'Available'), 0)   AS available_slots,
       COALESCE(SUM(c.display_status = 'Held'), 0)        AS held_slots,
       COALESCE(SUM(c.display_status = 'Reserved'), 0)    AS reserved_slots,
       COALESCE(SUM(c.display_status = 'Occupied'), 0)    AS occupied_slots,
       COALESCE(SUM(c.display_status = 'Maintenance'), 0) AS maintenance_slots
FROM Parking_Lots l
LEFT JOIN v_slot_current_status c ON c.lot_id = l.lot_id
GROUP BY l.lot_id, l.lot_name, l.is_active;

-- Reference queries for the backend (not executed here, use parameters)
-- (A) free slots in a lot for the range [:start, :end)
--   SELECT s.slot_id, s.slot_code
--   FROM Parking_Slots s
--   WHERE s.lot_id = :lot_id AND s.is_deleted = 0 AND s.slot_status = 'Active'
--     AND NOT EXISTS (
--       SELECT 1 FROM Reservations r
--       WHERE r.slot_id = s.slot_id AND r.start_time < :end
--         AND ( (r.status = 'Confirmed' AND r.end_time > :start)
--            OR (r.status = 'Held' AND r.end_time > :start AND r.hold_expires_at > :now)
--            OR (r.status = 'In Use' AND GREATEST(r.end_time, :now) > :start) ));
--
-- (B) same vehicle already booked in that range?
--   SELECT 1 FROM Reservations
--   WHERE vehicle_id = :vehicle_id AND start_time < :end AND end_time > :start
--     AND (status IN ('Confirmed','In Use') OR (status = 'Held' AND hold_expires_at > :now))
--   LIMIT 1;
--
-- (C) active reservations of a customer (for the max-3 rule)
--   SELECT COUNT(*) FROM Reservations
--   WHERE user_id = :user_id
--     AND (status IN ('Confirmed','In Use') OR (status = 'Held' AND hold_expires_at > :now));
--
-- (D) scheduler (each in a transaction, write an outbox row per changed reservation)
--   Held -> Expired:      status = 'Held' AND hold_expires_at <= :now
--   Confirmed -> No-show: status = 'Confirmed' AND start_time + INTERVAL :grace MINUTE <= :now
--   ending soon:          status = 'In Use' AND end_warning_sent = 0
--                         AND end_time <= :now + INTERVAL :warn MINUTE
--   overstay:             status = 'In Use' AND overstay_notified = 0 AND end_time <= :now
