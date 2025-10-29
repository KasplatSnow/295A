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

export const cameras = pgTable("cameras", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  name: text("name").notNull(),
  location: text("location").notNull(),
  streamUrl: text("stream_url").notNull(),
  status: text("status").notNull().default("active"),
  addedOn: timestamp("added_on").notNull().defaultNow(),
});

export const incidents = pgTable("incidents", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  incidentNumber: integer("incident_number").notNull(),
  type: text("type").notNull(),
  location: text("location").notNull(),
  confidence: integer("confidence").notNull(),
  status: text("status").notNull().default("active"),
  detectedAt: timestamp("detected_at").notNull().defaultNow(),
  resolvedAt: timestamp("resolved_at"),
  entities: text("entities"),
  snapshotUrl: text("snapshot_url"),
});

export const insertUserSchema = createInsertSchema(users).omit({ id: true });
export const insertCameraSchema = createInsertSchema(cameras).omit({ id: true, addedOn: true });
export const insertIncidentSchema = createInsertSchema(incidents).omit({ id: true, detectedAt: true });

export type InsertUser = z.infer<typeof insertUserSchema>;
export type User = typeof users.$inferSelect;
export type Camera = typeof cameras.$inferSelect;
export type InsertCamera = z.infer<typeof insertCameraSchema>;
export type Incident = typeof incidents.$inferSelect;
export type InsertIncident = z.infer<typeof insertIncidentSchema>;
