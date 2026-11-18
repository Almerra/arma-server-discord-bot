CREATE TABLE IF NOT EXISTS `server_authentication` (
  `user_id` varchar(20) NOT NULL,
  `guild_id` varchar(20) NOT NULL,
  `server_type` varchar(20) NOT NULL,
  `server_username` varchar(255) NOT NULL,
  `server_password` varchar(255) NOT NULL
);