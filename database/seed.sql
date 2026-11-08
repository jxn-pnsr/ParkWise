-- ParkWise sample data (for development and demo)
--
-- Run after schema.sql:
--   mysql -u root -p --default-character-set=utf8mb4 parkwise < database/seed.sql
-- Then run database/seed_mongo.py to fill MongoDB.
--
-- * Safe to run again: it clears the data first (System_Config is kept).
-- * All times are relative to the moment you run it (UTC), so the demo
--   data always looks "current".
-- * Every account uses the password: Passw0rd!
--
-- Accounts:
--   admin@parkwise.local                          Admin
--   staff1@parkwise.local, staff2@parkwise.local  Staff
--   somchai@example.com, suda@example.com, anan@example.com,
--   malee@example.com, preecha@example.com        Customer

SET NAMES utf8mb4;
SET time_zone = '+00:00';
USE parkwise;

SET @now = TIMESTAMP(DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:00'));
SET @iso = '%Y-%m-%dT%H:%i:%sZ';
SET @pw  = 'pbkdf2:sha256:1000000$w1zQVoOofunQ36rp$ee34084da0fd58c28a125df2fdac617ca7c387f38d94ac93dc50a03b256d2b1b';

SET FOREIGN_KEY_CHECKS = 0;
TRUNCATE TABLE Integration_Outbox;
TRUNCATE TABLE Payments;
TRUNCATE TABLE Reservations;
TRUNCATE TABLE Parking_Slots;
TRUNCATE TABLE Parking_Lots;
TRUNCATE TABLE Customer_Vehicles;
TRUNCATE TABLE Users;
SET FOREIGN_KEY_CHECKS = 1;

-- Users: 1 admin, 2 staff, 5 customers
INSERT INTO Users (user_id, email, password_hash, full_name, phone, role, is_active, created_at) VALUES
 (1, 'admin@parkwise.local',  @pw, 'System Admin',     '020000001',  'Admin',    1, @now - INTERVAL 60 DAY),
 (2, 'staff1@parkwise.local', @pw, 'Wichai Prasert',   '020000002',  'Staff',    1, @now - INTERVAL 59 DAY),
 (3, 'staff2@parkwise.local', @pw, 'Napa Suksan',      '020000003',  'Staff',    1, @now - INTERVAL 59 DAY),
 (4, 'somchai@example.com',   @pw, 'Somchai Jaidee',   '0811111111', 'Customer', 1, @now - INTERVAL 30 DAY),
 (5, 'suda@example.com',      @pw, 'Suda Raksa',       '0822222222', 'Customer', 1, @now - INTERVAL 28 DAY),
 (6, 'anan@example.com',      @pw, 'Anan Mankhong',    '0833333333', 'Customer', 1, @now - INTERVAL 20 DAY),
 (7, 'malee@example.com',     @pw, 'Malee Sodsai',     '0844444444', 'Customer', 1, @now - INTERVAL 15 DAY),
 (8, 'preecha@example.com',   @pw, 'Preecha Kengkla',  '0855555555', 'Customer', 1, @now - INTERVAL 10 DAY);

-- Vehicles: 8 active + 1 soft-deleted (to demo the delete button)
INSERT INTO Customer_Vehicles (vehicle_id, user_id, plate_number, province, is_default, is_deleted, created_at) VALUES
 (1, 4, '1AB1234', 'Bangkok',       1, 0, @now - INTERVAL 30 DAY),
 (2, 4, '2CD5678', 'Bangkok',       0, 0, @now - INTERVAL 29 DAY),
 (3, 5, '3EF9012', 'Nonthaburi',    1, 0, @now - INTERVAL 28 DAY),
 (4, 5, '4GH3456', 'Pathum Thani',  0, 0, @now - INTERVAL 27 DAY),
 (5, 6, '5JK7890', 'Bangkok',       1, 0, @now - INTERVAL 20 DAY),
 (6, 7, '6LM1357', 'Chiang Mai',    1, 0, @now - INTERVAL 15 DAY),
 (7, 7, '7NP2468', 'Chonburi',      0, 0, @now - INTERVAL 14 DAY),
 (8, 8, '8QR1111', 'Samut Prakan',  1, 0, @now - INTERVAL 10 DAY),
 (9, 5, '9ST0000', 'Bangkok',       0, 1, @now - INTERVAL 26 DAY);   -- deleted

-- 2 lots, 10 slots each (slot_id 1-10 = A1..A10, 11-20 = B1..B10, B10 is under maintenance)
INSERT INTO Parking_Lots (lot_id, lot_name, location_desc, is_active) VALUES
 (1, 'Lot A', 'Building A, ground floor near the main entrance', 1),
 (2, 'Lot B', 'Open-air lot behind the building',                1);

INSERT INTO Parking_Slots (slot_id, lot_id, slot_code, slot_status) VALUES
 (1,1,'A1','Active'),(2,1,'A2','Active'),(3,1,'A3','Active'),(4,1,'A4','Active'),(5,1,'A5','Active'),
 (6,1,'A6','Active'),(7,1,'A7','Active'),(8,1,'A8','Active'),(9,1,'A9','Active'),(10,1,'A10','Active'),
 (11,2,'B1','Active'),(12,2,'B2','Active'),(13,2,'B3','Active'),(14,2,'B4','Active'),(15,2,'B5','Active'),
 (16,2,'B6','Active'),(17,2,'B7','Active'),(18,2,'B8','Active'),(19,2,'B9','Active'),(20,2,'B10','Maintenance');

-- Reservations (times are minutes relative to now)
--
--  id  user veh slot type     start..end         status     used for
--  1   4    1   A1   Instant  -60..+60           In Use     Occupied slot
--  2   5    3   A2   Advance  +180..+300         Confirmed  future booking
--  3   6    5   A3   Instant  -10..+110          Confirmed  Reserved slot, no-show soon
--  4   7    6   A4   Instant  -2..+58            Held       Held slot / countdown
--  5   4    1   A5   Advance  -300..-180         Completed  overstay example (140 baht)
--  6   5    4   A6   Advance  -1620..-1500       Completed  paid (cash)
--  7   8    8   A7   Instant  -175..-115         Completed  payment pending
--  8   6    5   B1   Advance  +1440..+1620       Cancelled  cancelled by customer
--  9   7    7   B2   Advance  +2880..+3120       Confirmed  booking in 2 days
--  10  8    8   B3   Instant  -60..0             Expired    hold was not confirmed
--  11  5    3   B4   Advance  -180..-120         No-show    never checked in
--  12  4    2   B5   Advance  -240..-60          In Use     currently overstaying
--  13  5    4   B5   Advance  +30..+150          Confirmed  next booking hit by overstay
--  14  6    5   A8   Advance  -3000..-2940       Completed  payment failed
INSERT INTO Reservations
 (reservation_id, user_id, vehicle_id, slot_id, booking_type, start_time, end_time, duration_hours, status,
  hold_expires_at, confirmed_at, checked_in_at, checked_out_at, checked_in_by, checked_out_by,
  cancelled_at, cancelled_by, cancel_reason, is_overstay, end_warning_sent, overstay_notified, created_at)
VALUES
 (1, 4, 1, 1,  'Instant', @now - INTERVAL 60 MINUTE,  @now + INTERVAL 60 MINUTE,  2, 'In Use',
  @now - INTERVAL 60 MINUTE, @now - INTERVAL 63 MINUTE, @now - INTERVAL 55 MINUTE, NULL, 2, NULL,
  NULL, NULL, NULL, 0, 0, 0, @now - INTERVAL 65 MINUTE),

 (2, 5, 3, 2,  'Advance', @now + INTERVAL 180 MINUTE, @now + INTERVAL 300 MINUTE, 2, 'Confirmed',
  @now - INTERVAL 25 MINUTE, @now - INTERVAL 28 MINUTE, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, 0, 0, 0, @now - INTERVAL 30 MINUTE),

 (3, 6, 5, 3,  'Instant', @now - INTERVAL 10 MINUTE,  @now + INTERVAL 110 MINUTE, 2, 'Confirmed',
  @now - INTERVAL 5 MINUTE, @now - INTERVAL 8 MINUTE, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, 0, 0, 0, @now - INTERVAL 10 MINUTE),

 (4, 7, 6, 4,  'Instant', @now - INTERVAL 2 MINUTE,   @now + INTERVAL 58 MINUTE,  1, 'Held',
  @now + INTERVAL 3 MINUTE, NULL, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, 0, 0, 0, @now - INTERVAL 2 MINUTE),

 (5, 4, 1, 5,  'Advance', @now - INTERVAL 300 MINUTE, @now - INTERVAL 180 MINUTE, 2, 'Completed',
  @now - INTERVAL 1445 MINUTE, @now - INTERVAL 1440 MINUTE, @now - INTERVAL 295 MINUTE, @now - INTERVAL 90 MINUTE, 2, 3,
  NULL, NULL, NULL, 1, 1, 1, @now - INTERVAL 1450 MINUTE),

 (6, 5, 4, 6,  'Advance', @now - INTERVAL 1620 MINUTE, @now - INTERVAL 1500 MINUTE, 2, 'Completed',
  @now - INTERVAL 2000 MINUTE, @now - INTERVAL 1995 MINUTE, @now - INTERVAL 1618 MINUTE, @now - INTERVAL 1510 MINUTE, 3, 3,
  NULL, NULL, NULL, 0, 1, 0, @now - INTERVAL 2005 MINUTE),

 (7, 8, 8, 7,  'Instant', @now - INTERVAL 175 MINUTE, @now - INTERVAL 115 MINUTE, 1, 'Completed',
  @now - INTERVAL 170 MINUTE, @now - INTERVAL 173 MINUTE, @now - INTERVAL 170 MINUTE, @now - INTERVAL 125 MINUTE, 2, 2,
  NULL, NULL, NULL, 0, 0, 0, @now - INTERVAL 175 MINUTE),

 (8, 6, 5, 11, 'Advance', @now + INTERVAL 1440 MINUTE, @now + INTERVAL 1620 MINUTE, 3, 'Cancelled',
  @now - INTERVAL 55 MINUTE, @now - INTERVAL 58 MINUTE, NULL, NULL, NULL, NULL,
  @now - INTERVAL 20 MINUTE, 6, 'Change of plans', 0, 0, 0, @now - INTERVAL 60 MINUTE),

 (9, 7, 7, 12, 'Advance', @now + INTERVAL 2880 MINUTE, @now + INTERVAL 3120 MINUTE, 4, 'Confirmed',
  @now - INTERVAL 10 MINUTE, @now - INTERVAL 12 MINUTE, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, 0, 0, 0, @now - INTERVAL 15 MINUTE),

 (10, 8, 8, 13, 'Instant', @now - INTERVAL 60 MINUTE, @now,                        1, 'Expired',
  @now - INTERVAL 55 MINUTE, NULL, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, 0, 0, 0, @now - INTERVAL 60 MINUTE),

 (11, 5, 3, 14, 'Advance', @now - INTERVAL 180 MINUTE, @now - INTERVAL 120 MINUTE, 1, 'No-show',
  @now - INTERVAL 255 MINUTE, @now - INTERVAL 250 MINUTE, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, 0, 0, 0, @now - INTERVAL 260 MINUTE),

 (12, 4, 2, 15, 'Advance', @now - INTERVAL 240 MINUTE, @now - INTERVAL 60 MINUTE,  3, 'In Use',
  @now - INTERVAL 1005 MINUTE, @now - INTERVAL 1000 MINUTE, @now - INTERVAL 235 MINUTE, NULL, 3, NULL,
  NULL, NULL, NULL, 1, 1, 1, @now - INTERVAL 1010 MINUTE),

 (13, 5, 4, 15, 'Advance', @now + INTERVAL 30 MINUTE,  @now + INTERVAL 150 MINUTE, 2, 'Confirmed',
  @now - INTERVAL 22 MINUTE, @now - INTERVAL 25 MINUTE, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, 0, 0, 0, @now - INTERVAL 27 MINUTE),

 (14, 6, 5, 8,  'Advance', @now - INTERVAL 3000 MINUTE, @now - INTERVAL 2940 MINUTE, 1, 'Completed',
  @now - INTERVAL 3010 MINUTE, @now - INTERVAL 3005 MINUTE, @now - INTERVAL 2998 MINUTE, @now - INTERVAL 2945 MINUTE, 2, 2,
  NULL, NULL, NULL, 0, 0, 0, @now - INTERVAL 3015 MINUTE);

-- Payments (one Paid by QR, one Paid by cash, one Pending, one Failed)
INSERT INTO Payments (payment_id, reservation_id, user_id, base_fee, overstay_fee, total_amount, method, status, paid_at, created_at) VALUES
 (1, 5,  4, 40.00, 100.00, 140.00, 'QR',   'Paid',    @now - INTERVAL 80 MINUTE,   @now - INTERVAL 90 MINUTE),
 (2, 6,  5, 40.00,   0.00,  40.00, 'Cash', 'Paid',    @now - INTERVAL 1505 MINUTE, @now - INTERVAL 1510 MINUTE),
 (3, 7,  8, 20.00,   0.00,  20.00, NULL,   'Pending', NULL,                        @now - INTERVAL 125 MINUTE),
 (4, 14, 6, 20.00,   0.00,  20.00, 'QR',   'Failed',  NULL,                        @now - INTERVAL 2945 MINUTE);

-- Integration_Outbox: 16 Sent, 1 Pending, 1 Failed
-- The event ids are fixed so seed_mongo.py gives the same result every time.
-- The Pending and Failed rows are not in MongoDB on purpose, so the admin
-- outbox page and the Retry button have something to show.
INSERT INTO Integration_Outbox (event_id, event_type, aggregate_id, payload, status, retry_count, last_error, created_at, sent_at) VALUES
 ('00000000-0000-4000-8000-000000000001','user.login','4',
  JSON_OBJECT('entity_type','user','entity_id',4,'actor_user_id',4,'actor_role','Customer','ip_address','192.168.1.21',
              'metadata',JSON_OBJECT('user_agent','Chrome/Windows')),
  'Sent',0,NULL,@now - INTERVAL 70 MINUTE,@now - INTERVAL 70 MINUTE),

 ('00000000-0000-4000-8000-000000000002','reservation.held','1',
  JSON_OBJECT('entity_type','reservation','entity_id',1,'actor_user_id',4,'actor_role','Customer','ip_address','192.168.1.21',
              'metadata',JSON_OBJECT('slot','A1','lot','Lot A','booking_type','Instant','hold_minutes',5)),
  'Sent',0,NULL,@now - INTERVAL 65 MINUTE,@now - INTERVAL 65 MINUTE),

 ('00000000-0000-4000-8000-000000000003','reservation.confirmed','1',
  JSON_OBJECT('entity_type','reservation','entity_id',1,'actor_user_id',4,'actor_role','Customer','ip_address','192.168.1.21',
              'target_user_id',4,
              'metadata',JSON_OBJECT('slot','A1','lot','Lot A'),
              'notify',JSON_OBJECT('type','reservation_confirmed','title','Reservation confirmed',
                                   'message','Your reservation for slot A1 (Lot A) is confirmed. Please check in within 30 minutes.')),
  'Sent',0,NULL,@now - INTERVAL 63 MINUTE,@now - INTERVAL 63 MINUTE),

 ('00000000-0000-4000-8000-000000000004','checkin.done','1',
  JSON_OBJECT('entity_type','reservation','entity_id',1,'actor_user_id',2,'actor_role','Staff','ip_address','192.168.1.5',
              'target_user_id',4,
              'metadata',JSON_OBJECT('slot','A1','lot','Lot A','plate','1AB1234'),
              'notify',JSON_OBJECT('type','checkin_done','title','Check-in successful',
                                   'message','Vehicle 1AB1234 has checked in at slot A1 (Lot A).'),
              'session',JSON_OBJECT('reservationId',1,'userId',4,'vehicleId',1,'plateNumber','1AB1234',
                                    'lotName','Lot A','slotCode','A1',
                                    'checkInAt',DATE_FORMAT(@now - INTERVAL 55 MINUTE,@iso),'checkOutAt',NULL,
                                    'durationMinutes',NULL,'isOverstay',FALSE)),
  'Sent',0,NULL,@now - INTERVAL 55 MINUTE,@now - INTERVAL 55 MINUTE),

 ('00000000-0000-4000-8000-000000000005','reservation.held','4',
  JSON_OBJECT('entity_type','reservation','entity_id',4,'actor_user_id',7,'actor_role','Customer','ip_address','192.168.1.40',
              'metadata',JSON_OBJECT('slot','A4','lot','Lot A','booking_type','Instant','hold_minutes',5)),
  'Sent',0,NULL,@now - INTERVAL 2 MINUTE,@now - INTERVAL 2 MINUTE),

 ('00000000-0000-4000-8000-000000000006','reservation.expired','10',
  JSON_OBJECT('entity_type','reservation','entity_id',10,'actor_user_id',NULL,'actor_role','System',
              'target_user_id',8,
              'metadata',JSON_OBJECT('slot','B3','lot','Lot B'),
              'notify',JSON_OBJECT('type','hold_expired','title','Reservation expired',
                                   'message','You did not confirm slot B3 (Lot B) within 5 minutes, so the hold was released.')),
  'Sent',0,NULL,@now - INTERVAL 55 MINUTE,@now - INTERVAL 55 MINUTE),

 ('00000000-0000-4000-8000-000000000007','reservation.no_show','11',
  JSON_OBJECT('entity_type','reservation','entity_id',11,'actor_user_id',NULL,'actor_role','System',
              'target_user_id',5,
              'metadata',JSON_OBJECT('slot','B4','lot','Lot B','grace_minutes',30),
              'notify',JSON_OBJECT('type','no_show','title','Reservation marked as no-show',
                                   'message','You did not check in within 30 minutes, so your reservation for slot B4 (Lot B) was cancelled.')),
  'Sent',0,NULL,@now - INTERVAL 150 MINUTE,@now - INTERVAL 150 MINUTE),

 ('00000000-0000-4000-8000-000000000008','checkin.done','5',
  JSON_OBJECT('entity_type','reservation','entity_id',5,'actor_user_id',2,'actor_role','Staff','ip_address','192.168.1.5',
              'target_user_id',4,
              'metadata',JSON_OBJECT('slot','A5','lot','Lot A','plate','1AB1234'),
              'notify',JSON_OBJECT('type','checkin_done','title','Check-in successful',
                                   'message','Vehicle 1AB1234 has checked in at slot A5 (Lot A).'),
              'session',JSON_OBJECT('reservationId',5,'userId',4,'vehicleId',1,'plateNumber','1AB1234',
                                    'lotName','Lot A','slotCode','A5',
                                    'checkInAt',DATE_FORMAT(@now - INTERVAL 295 MINUTE,@iso),'checkOutAt',NULL,
                                    'durationMinutes',NULL,'isOverstay',FALSE)),
  'Sent',0,NULL,@now - INTERVAL 295 MINUTE,@now - INTERVAL 295 MINUTE),

 ('00000000-0000-4000-8000-000000000009','overstay.detected','5',
  JSON_OBJECT('entity_type','reservation','entity_id',5,'actor_user_id',NULL,'actor_role','System',
              'target_user_id',4,
              'metadata',JSON_OBJECT('slot','A5','lot','Lot A'),
              'notify',JSON_OBJECT('type','overstay','title','Parking time exceeded',
                                   'message','Your booking for slot A5 has ended. Extra time is charged at 40/60 baht per hour.')),
  'Sent',0,NULL,@now - INTERVAL 180 MINUTE,@now - INTERVAL 180 MINUTE),

 ('00000000-0000-4000-8000-000000000010','checkout.done','5',
  JSON_OBJECT('entity_type','reservation','entity_id',5,'actor_user_id',3,'actor_role','Staff','ip_address','192.168.1.6',
              'target_user_id',4,
              'metadata',JSON_OBJECT('slot','A5','lot','Lot A','total',140),
              'notify',JSON_OBJECT('type','checkout_done','title','Check-out successful',
                                   'message','Total fee is 140 baht (normal 40 + overstay 100). Please pay now.'),
              'session',JSON_OBJECT('reservationId',5,'userId',4,'vehicleId',1,'plateNumber','1AB1234',
                                    'lotName','Lot A','slotCode','A5',
                                    'checkInAt',DATE_FORMAT(@now - INTERVAL 295 MINUTE,@iso),
                                    'checkOutAt',DATE_FORMAT(@now - INTERVAL 90 MINUTE,@iso),
                                    'durationMinutes',205,
                                    'fees',JSON_OBJECT('base',40,'overstay',100,'total',140),'isOverstay',TRUE)),
  'Sent',0,NULL,@now - INTERVAL 90 MINUTE,@now - INTERVAL 90 MINUTE),

 ('00000000-0000-4000-8000-000000000011','payment.paid','1',
  JSON_OBJECT('entity_type','payment','entity_id',1,'actor_user_id',4,'actor_role','Customer','ip_address','192.168.1.21',
              'target_user_id',4,
              'metadata',JSON_OBJECT('reservation_id',5,'method','QR','total',140),
              'notify',JSON_OBJECT('type','payment_paid','title','Payment successful',
                                   'message','Your payment of 140 baht (QR) was received.')),
  'Sent',0,NULL,@now - INTERVAL 80 MINUTE,@now - INTERVAL 80 MINUTE),

 ('00000000-0000-4000-8000-000000000012','reservation.confirmed','3',
  JSON_OBJECT('entity_type','reservation','entity_id',3,'actor_user_id',6,'actor_role','Customer','ip_address','192.168.1.33',
              'target_user_id',6,
              'metadata',JSON_OBJECT('slot','A3','lot','Lot A'),
              'notify',JSON_OBJECT('type','reservation_confirmed','title','Reservation confirmed',
                                   'message','Your reservation for slot A3 (Lot A) is confirmed. Please check in within 30 minutes.')),
  'Sent',0,NULL,@now - INTERVAL 8 MINUTE,@now - INTERVAL 8 MINUTE),

 ('00000000-0000-4000-8000-000000000013','checkin.done','12',
  JSON_OBJECT('entity_type','reservation','entity_id',12,'actor_user_id',3,'actor_role','Staff','ip_address','192.168.1.6',
              'target_user_id',4,
              'metadata',JSON_OBJECT('slot','B5','lot','Lot B','plate','2CD5678'),
              'notify',JSON_OBJECT('type','checkin_done','title','Check-in successful',
                                   'message','Vehicle 2CD5678 has checked in at slot B5 (Lot B).'),
              'session',JSON_OBJECT('reservationId',12,'userId',4,'vehicleId',2,'plateNumber','2CD5678',
                                    'lotName','Lot B','slotCode','B5',
                                    'checkInAt',DATE_FORMAT(@now - INTERVAL 235 MINUTE,@iso),'checkOutAt',NULL,
                                    'durationMinutes',NULL,'isOverstay',TRUE)),
  'Sent',0,NULL,@now - INTERVAL 235 MINUTE,@now - INTERVAL 235 MINUTE),

 ('00000000-0000-4000-8000-000000000014','checkout.done','6',
  JSON_OBJECT('entity_type','reservation','entity_id',6,'actor_user_id',3,'actor_role','Staff','ip_address','192.168.1.6',
              'target_user_id',5,
              'metadata',JSON_OBJECT('slot','A6','lot','Lot A','total',40),
              'notify',JSON_OBJECT('type','checkout_done','title','Check-out successful',
                                   'message','Total fee is 40 baht. Please pay now.'),
              'session',JSON_OBJECT('reservationId',6,'userId',5,'vehicleId',4,'plateNumber','4GH3456',
                                    'lotName','Lot A','slotCode','A6',
                                    'checkInAt',DATE_FORMAT(@now - INTERVAL 1618 MINUTE,@iso),
                                    'checkOutAt',DATE_FORMAT(@now - INTERVAL 1510 MINUTE,@iso),
                                    'durationMinutes',108,
                                    'fees',JSON_OBJECT('base',40,'overstay',0,'total',40),'isOverstay',FALSE)),
  'Sent',0,NULL,@now - INTERVAL 1510 MINUTE,@now - INTERVAL 1510 MINUTE),

 ('00000000-0000-4000-8000-000000000015','checkout.done','7',
  JSON_OBJECT('entity_type','reservation','entity_id',7,'actor_user_id',2,'actor_role','Staff','ip_address','192.168.1.5',
              'target_user_id',8,
              'metadata',JSON_OBJECT('slot','A7','lot','Lot A','total',20),
              'notify',JSON_OBJECT('type','checkout_done','title','Check-out successful',
                                   'message','Total fee is 20 baht. Please pay now.'),
              'session',JSON_OBJECT('reservationId',7,'userId',8,'vehicleId',8,'plateNumber','8QR1111',
                                    'lotName','Lot A','slotCode','A7',
                                    'checkInAt',DATE_FORMAT(@now - INTERVAL 170 MINUTE,@iso),
                                    'checkOutAt',DATE_FORMAT(@now - INTERVAL 125 MINUTE,@iso),
                                    'durationMinutes',45,
                                    'fees',JSON_OBJECT('base',20,'overstay',0,'total',20),'isOverstay',FALSE)),
  'Sent',0,NULL,@now - INTERVAL 125 MINUTE,@now - INTERVAL 125 MINUTE),

 ('00000000-0000-4000-8000-000000000016','checkout.done','14',
  JSON_OBJECT('entity_type','reservation','entity_id',14,'actor_user_id',2,'actor_role','Staff','ip_address','192.168.1.5',
              'target_user_id',6,
              'metadata',JSON_OBJECT('slot','A8','lot','Lot A','total',20),
              'notify',JSON_OBJECT('type','checkout_done','title','Check-out successful',
                                   'message','Total fee is 20 baht. Please pay now.'),
              'session',JSON_OBJECT('reservationId',14,'userId',6,'vehicleId',5,'plateNumber','5JK7890',
                                    'lotName','Lot A','slotCode','A8',
                                    'checkInAt',DATE_FORMAT(@now - INTERVAL 2998 MINUTE,@iso),
                                    'checkOutAt',DATE_FORMAT(@now - INTERVAL 2945 MINUTE,@iso),
                                    'durationMinutes',53,
                                    'fees',JSON_OBJECT('base',20,'overstay',0,'total',20),'isOverstay',FALSE)),
  'Sent',0,NULL,@now - INTERVAL 2945 MINUTE,@now - INTERVAL 2945 MINUTE),

 -- Pending: not sent to MongoDB yet
 ('00000000-0000-4000-8000-000000000017','reservation.confirmed','13',
  JSON_OBJECT('entity_type','reservation','entity_id',13,'actor_user_id',5,'actor_role','Customer','ip_address','192.168.1.22',
              'target_user_id',5,
              'metadata',JSON_OBJECT('slot','B5','lot','Lot B'),
              'notify',JSON_OBJECT('type','reservation_confirmed','title','Reservation confirmed',
                                   'message','Your reservation for slot B5 (Lot B) is confirmed.')),
  'Pending',0,NULL,@now - INTERVAL 25 MINUTE,NULL),

 -- Failed: tried 5 times, the admin can press Retry
 ('00000000-0000-4000-8000-000000000018','reservation.cancelled','8',
  JSON_OBJECT('entity_type','reservation','entity_id',8,'actor_user_id',6,'actor_role','Customer','ip_address','192.168.1.33',
              'target_user_id',6,
              'metadata',JSON_OBJECT('slot','B1','lot','Lot B','reason','Change of plans'),
              'notify',JSON_OBJECT('type','reservation_cancelled','title','Reservation cancelled',
                                   'message','Your reservation for slot B1 (Lot B) was cancelled as requested.')),
  'Failed',5,'pymongo.errors.ServerSelectionTimeoutError: localhost:27017: [Errno 111] Connection refused',
  @now - INTERVAL 20 MINUTE,NULL);

-- quick row count check
SELECT 'Users' AS tbl, COUNT(*) AS n FROM Users
UNION ALL SELECT 'Customer_Vehicles', COUNT(*) FROM Customer_Vehicles
UNION ALL SELECT 'Parking_Lots', COUNT(*) FROM Parking_Lots
UNION ALL SELECT 'Parking_Slots', COUNT(*) FROM Parking_Slots
UNION ALL SELECT 'Reservations', COUNT(*) FROM Reservations
UNION ALL SELECT 'Payments', COUNT(*) FROM Payments
UNION ALL SELECT 'Integration_Outbox', COUNT(*) FROM Integration_Outbox;
