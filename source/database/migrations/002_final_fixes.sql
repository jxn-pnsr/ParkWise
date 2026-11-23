-- M11: a plate + province is unique per customer, not across all customers.
ALTER TABLE Customer_Vehicles DROP INDEX uq_vehicle_active_plate;
ALTER TABLE Customer_Vehicles DROP COLUMN active_plate_key;
ALTER TABLE Customer_Vehicles
  ADD COLUMN active_plate_key VARCHAR(100)
    GENERATED ALWAYS AS (IF(is_deleted = 0, CONCAT(user_id, '|', plate_number, '|', province), NULL)) VIRTUAL,
  ADD UNIQUE KEY uq_vehicle_active_plate (active_plate_key);

-- M4: customers with unpaid parking cannot book again (admin can switch it off).
INSERT IGNORE INTO System_Config (config_key, config_value, description) VALUES
 ('BLOCK_BOOKING_WHEN_UNPAID', 