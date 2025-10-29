import { sql } from "drizzle-orm";
import { pgTable, text, varchar, timestamp, integer, boolean } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod";

export const users = pgTable("users", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  username: text("username").notNull().unique(),
  password: text("password").notNull(),
  fullName: text("full_name"),
  email: text("email"),
});

export const zones = pgTable("zones", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  name: text("name").notNull(),
  type: text("type").notNull(), // 'home', 'street', 'shared'
  description: text("description"),
  blurFaces: boolean("blur_faces").default(false),
  shareOnlyIncidents: boolean("share_only_incidents").default(false),
  disableAudio: boolean("disable_audio").default(false),
});

export const entities = pgTable("entities", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  name: text("name").notNull(),
  type: text("type").notNull(), // 'person', 'pet', 'vehicle'
  group: text("group").notNull(), // 'household', 'neighbor', 'watchlist'
  notes: text("notes"),
  imageUrls: text("image_urls").array(),
  lastSeen: timestamp("last_seen"),
  createdAt: timestamp("created_at").notNull().defaultNow(),
  consentObtained: boolean("consent_obtained").default(false),
});

export const cameras = pgTable("cameras", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  name: text("name").notNull(),
  location: text("location").notNull(),
  streamUrl: text("stream_url").notNull(),
  status: text("status").notNull().default("active"),
  zoneId: varchar("zone_id"),
  addedOn: timestamp("added_on").notNull().defaultNow(),
});

export const incidents = pgTable("incidents", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  incidentNumber: integer("incident_number").notNull(),
  type: text("type").notNull(),
  location: text("location").notNull(),
  confidence: integer("confidence").notNull(),
  cameraId: varchar("camera_id"),
  zoneId: varchar("zone_id"),
  entityId: varchar("entity_id"),
  entityMatch: text("entity_match"), // Suggested entity match
  entityMatchConfidence: integer("entity_match_confidence"),
  detectedAt: timestamp("detected_at").notNull().defaultNow(),
  resolvedAt: timestamp("resolved_at"),
  acknowledgedAt: timestamp("acknowledged_at"),
  snapshotUrl: text("snapshot_url"),
  classification: text("classification").array(), // ['fire', 'smoke']
});

export const communityMembers = pgTable("community_members", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  name: text("name").notNull(),
  email: text("email").notNull(),
  role: text("role").notNull(), // 'owner', 'admin', 'member', 'viewer'
  joinedAt: timestamp("joined_at").notNull().defaultNow(),
  invitedBy: varchar("invited_by"),
});

export const sharedCameras = pgTable("shared_cameras", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  cameraId: varchar("camera_id").notNull(),
  sharedWith: text("shared_with").notNull(), // user id or 'group:neighborhood'
  zoneId: varchar("zone_id"),
  createdAt: timestamp("created_at").notNull().defaultNow(),
});

export const auditLogs = pgTable("audit_logs", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  timestamp: timestamp("timestamp").notNull().defaultNow(),
  userId: varchar("user_id").notNull(),
  action: text("action").notNull(),
  details: text("details"),
});

export const entitySightings = pgTable("entity_sightings", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  entityId: varchar("entity_id").notNull(),
  cameraId: varchar("camera_id").notNull(),
  confidence: integer("confidence").notNull(),
  timestamp: timestamp("timestamp").notNull().defaultNow(),
});

// Insert schemas
export const insertUserSchema = createInsertSchema(users).omit({ id: true });
export const insertZoneSchema = createInsertSchema(zones).omit({ id: true });
export const insertEntitySchema = createInsertSchema(entities).omit({ id: true, createdAt: true, lastSeen: true });
export const insertCameraSchema = createInsertSchema(cameras).omit({ id: true, addedOn: true });
export const insertIncidentSchema = createInsertSchema(incidents).omit({ id: true, detectedAt: true });
export const insertCommunityMemberSchema = createInsertSchema(communityMembers).omit({ id: true, joinedAt: true });
export const insertSharedCameraSchema = createInsertSchema(sharedCameras).omit({ id: true, createdAt: true });
export const insertAuditLogSchema = createInsertSchema(auditLogs).omit({ id: true, timestamp: true });
export const insertEntitySightingSchema = createInsertSchema(entitySightings).omit({ id: true, timestamp: true });

// Types
export type InsertUser = z.infer<typeof insertUserSchema>;
export type User = typeof users.$inferSelect;
export type Zone = typeof zones.$inferSelect;
export type InsertZone = z.infer<typeof insertZoneSchema>;
export type Entity = typeof entities.$inferSelect;
export type InsertEntity = z.infer<typeof insertEntitySchema>;
export type Camera = typeof cameras.$inferSelect;
export type InsertCamera = z.infer<typeof insertCameraSchema>;
export type Incident = typeof incidents.$inferSelect;
export type InsertIncident = z.infer<typeof insertIncidentSchema>;
export type CommunityMember = typeof communityMembers.$inferSelect;
export type InsertCommunityMember = z.infer<typeof insertCommunityMemberSchema>;
export type SharedCamera = typeof sharedCameras.$inferSelect;
export type InsertSharedCamera = z.infer<typeof insertSharedCameraSchema>;
export type AuditLog = typeof auditLogs.$inferSelect;
export type InsertAuditLog = z.infer<typeof insertAuditLogSchema>;
export type EntitySighting = typeof entitySightings.$inferSelect;
export type InsertEntitySighting = z.infer<typeof insertEntitySightingSchema>;
