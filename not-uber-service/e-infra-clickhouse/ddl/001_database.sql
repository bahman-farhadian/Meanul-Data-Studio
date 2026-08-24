-- The one database every analytics table lives in.
--
-- ON CLUSTER means "run this on every node of nus_cluster". The statement is
-- put on a queue in Keeper, so a node that is briefly down still receives it
-- when it comes back. Every file in this directory uses it.
CREATE DATABASE IF NOT EXISTS nus ON CLUSTER nus_cluster;
