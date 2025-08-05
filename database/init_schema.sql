-- MySQL Workbench Forward Engineering

SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0;
SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0;
SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';

-- -----------------------------------------------------
-- Schema vehicle_data
-- -----------------------------------------------------

-- -----------------------------------------------------
-- Schema vehicle_data
-- -----------------------------------------------------
CREATE SCHEMA IF NOT EXISTS `vehicle_data` DEFAULT CHARACTER SET utf8mb4 ;
USE `vehicle_data` ;

-- -----------------------------------------------------
-- Table `vehicle_data`.`users`
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS `vehicle_data`.`users` (
  `user_id` INT NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(50) NOT NULL,
  `email` VARCHAR(100) NULL,
  `created_at` TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`),
  UNIQUE INDEX `idx_username_unique` (`username` ASC) VISIBLE,
  UNIQUE INDEX `idx_email_unique` (`email` ASC) VISIBLE)
ENGINE = InnoDB;


-- -----------------------------------------------------
-- Table `vehicle_data`.`vehicle_health`
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS `vehicle_data`.`vehicle_health` (
  `user_id` INT NOT NULL,
  `vin` VARCHAR(17) NOT NULL,
  `can_dump` TEXT NOT NULL,
  `updated_at` TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`),
  UNIQUE INDEX `vin_UNIQUE` (`vin` ASC) VISIBLE,
  CONSTRAINT `fk_user_message`
    FOREIGN KEY (`user_id`)
    REFERENCES `vehicle_data`.`users` (`user_id`)
    ON DELETE CASCADE
    ON UPDATE NO ACTION)
ENGINE = InnoDB;


SET SQL_MODE=@OLD_SQL_MODE;
SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS;
SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS;
