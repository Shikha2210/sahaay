import 'fake-indexeddb/auto';
import Dexie from 'dexie';
import { indexedDB, IDBKeyRange } from 'fake-indexeddb';
// Dexie reads its IndexedDB reference when first imported; point it at the in-memory fake explicitly.
Dexie.dependencies.indexedDB = indexedDB;
Dexie.dependencies.IDBKeyRange = IDBKeyRange;
