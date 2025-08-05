USE vehicle_data;

-- Disable foreign key checks to allow truncating tables potentially out of order
-- (though truncating child then parent is usually fine) and because TRUNCATE often ignores FKs anyway.
SET FOREIGN_KEY_CHECKS = 0;

-- Use TRUNCATE to empty tables AND reset AUTO_INCREMENT counters
TRUNCATE TABLE vehicle_health;
TRUNCATE TABLE users;

-- Re-enable foreign key checks
SET FOREIGN_KEY_CHECKS = 1;

-- Insert test users
-- Since the users table was truncated, AUTO_INCREMENT will likely reset to 1.
-- The first user will get ID 1, the second ID 2, etc.
INSERT INTO users (username, email) VALUES
  ('Agent Coulson', 'pcoulson@shield.gov'),  -- Should get user_id = 1
  ('James Bond', 'jbond@mi6.gov'),         -- Should get user_id = 2
  ('Agent Smith', 'asmith@matrix.io');      -- Should get user_id = 3

-- Insert vehicle health data (one per user)
-- The hardcoded user_ids (1, 2, 3) should now match the actual IDs
-- of the users just inserted above.
INSERT INTO vehicle_health (
    user_id, vin, updated_at,
    speed, rpm, temp, dtcs
) VALUES
-- Agent Coulson (references user_id 1)
(1, '1G1YR26R395800228',
 NOW(), '[30, 35, 40, 45]', '[1500, 1600, 1700, 1800]', '[85, 86, 87, 88]', '["P0301", "P0128"]'),

-- James Bond (references user_id 2)
(2, '1FTEW1CBXJFC29727',
 NOW(), '[50, 55, 60]', '[1800, 1900, 2000]', '[88, 89, 90]', '["P03D0"]'),

-- Agent Smith (references user_id 3)
(3, 'JTHHP5AY6JA003043',
 NOW(), '[20, 25, 30, 35]', '[1000, 1100, 1200, 1300]', '[80, 81, 82]', '[]');