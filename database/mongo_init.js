const dbp = db.getSiblingDB("parkwise");
const idType = ["int", "long"];

function ensureCollection(name, validator) {
  const opts = { validator: validator, validationLevel: "moderate", validationAction: "error" };
  if (dbp.getCollectionNames().includes(name)) {
    dbp.runCommand(Object.assign({ collMod: name }, opts));
    print("updated " + name);
  } else {
    dbp.createCollection(name, opts);
    print("created " + name);
  }
}

// Parking_Sessions: one session per reservation
ensureCollection("Parking_Sessions", { $jsonSchema: {
  bsonType: "object",
  required: ["userId", "plateNumber", "checkInAt", "createdAt"],
  properties: {
    reservationId:    { bsonType: idType },
    userId:           { bsonType: idType },
    vehicleId:        { bsonType: idType },
    plateNumber:      { bsonType: "string", minLength: 2 },
    lotName:          { bsonType: "string" },
    slotCode:         { bsonType: "string" },
    checkInAt:        { bsonType: "date" },
    checkOutAt:       { bsonType: ["date", "null"] },          // null while still parked
    durationMinutes:  { bsonType: ["int", "long", "null"], minimum: 0 },
    fees: {
      bsonType: "object",
      properties: {
        base:     { bsonType: ["int", "long", "double"], minimum: 0 },
        overstay: { bsonType: ["int", "long", "double"], minimum: 0 },
        total:    { bsonType: ["int", "long", "double"], minimum: 0 }
      }
    },
    isOverstay:       { bsonType: "bool" },
    source_event_ids: { bsonType: "array", items: { bsonType: "string" } },
    createdAt:        { bsonType: "date" },
    updatedAt:        { bsonType: "date" }
  }
}});
dbp.Parking_Sessions.createIndex({ reservationId: 1 },
  { name: "uq_session_reservation", unique: true,
    partialFilterExpression: { reservationId: { $exists: true } } });
dbp.Parking_Sessions.createIndex({ userId: 1, checkInAt: -1 },       { name: "ix_session_user_time" });
dbp.Parking_Sessions.createIndex({ plateNumber: 1, checkInAt: -1 },  { name: "ix_session_plate" });
dbp.Parking_Sessions.createIndex({ checkOutAt: 1 },                  { name: "ix_session_open" });

// Activity_Logs
ensureCollection("Activity_Logs", { $jsonSchema: {
  bsonType: "object",
  required: ["action_type", "timestamp"],
  properties: {
    userId:          { bsonType: ["int", "long", "null"] },    // null for system events
    role:            { bsonType: "string", enum: ["Customer", "Staff", "Admin", "System", "Anonymous"] },
    action_type:     { bsonType: "string", minLength: 3 },     // e.g. reservation.confirmed
    entity_type:     { bsonType: "string" },
    entity_id:       { bsonType: ["int", "long", "string", "null"] },
    ip_address:      { bsonType: ["string", "null"] },
    metadata:        { bsonType: "object" },
    timestamp:       { bsonType: "date" },
    admin_note:      { bsonType: ["string", "null"], maxLength: 500 },
    is_archived:     { bsonType: "bool" },
    source_event_id: { bsonType: "string" }
  }
}});
dbp.Activity_Logs.createIndex({ source_event_id: 1 },
  { name: "uq_log_event", unique: true,
    partialFilterExpression: { source_event_id: { $type: "string" } } });
dbp.Activity_Logs.createIndex({ timestamp: -1 },                 { name: "ix_log_time" });
dbp.Activity_Logs.createIndex({ userId: 1, timestamp: -1 },      { name: "ix_log_user_time" });
dbp.Activity_Logs.createIndex({ action_type: 1, timestamp: -1 }, { name: "ix_log_action_time" });
dbp.Activity_Logs.createIndex({ entity_type: 1, entity_id: 1 },  { name: "ix_log_entity" });
dbp.Activity_Logs.createIndex({ is_archived: 1, timestamp: -1 }, { name: "ix_log_archived" });

// Notifications
ensureCollection("Notifications", { $jsonSchema: {
  bsonType: "object",
  required: ["userId", "type", "title", "message", "isRead", "sentAt"],
  properties: {
    userId:          { bsonType: idType },
    type:            { bsonType: "string" },
    title:           { bsonType: "string", minLength: 1, maxLength: 150 },
    message:         { bsonType: "string", minLength: 1, maxLength: 1000 },
    isRead:          { bsonType: "bool" },
    reservationId:   { bsonType: ["int", "long", "null"] },
    sentAt:          { bsonType: "date" },
    readAt:          { bsonType: ["date", "null"] },
    source_event_id: { bsonType: "string" }
  }
}});
dbp.Notifications.createIndex({ userId: 1, isRead: 1, sentAt: -1 }, { name: "ix_notif_user_read_time" });
dbp.Notifications.createIndex({ source_event_id: 1 },
  { name: "uq_notif_event", unique: true,
    partialFilterExpression: { source_event_id: { $type: "string" } } });
dbp.Notifications.createIndex({ reservationId: 1 }, { name: "ix_notif_reservation", sparse: true });

// User_Preferences: one document per user
ensureCollection("User_Preferences", { $jsonSchema: {
  bsonType: "object",
  required: ["userId", "theme", "updatedAt"],
  properties: {
    userId:   { bsonType: idType },
    theme:    { bsonType: "string", enum: ["light", "dark"] },
    language: { bsonType: "string", enum: ["th", "en"] },
    notifications: {
      bsonType: "object",
      properties: {
        holdExpired: { bsonType: "bool" }, noShow:   { bsonType: "bool" },
        checkIn:     { bsonType: "bool" }, checkOut: { bsonType: "bool" },
        overstay:    { bsonType: "bool" }
      }
    },
    defaultVehicleId: { bsonType: ["int", "long", "null"] },
    updatedAt:        { bsonType: "date" }
  }
}});
dbp.User_Preferences.createIndex({ userId: 1 }, { name: "uq_pref_user", unique: true });

print("\nIndexes:");
["Parking_Sessions", "Activity_Logs", "Notifications", "User_Preferences"].forEach(function (c) {
  print("  " + c + ": " + dbp.getCollection(c).getIndexes().map(function (i) { return i.name; }).join(", "));
});
