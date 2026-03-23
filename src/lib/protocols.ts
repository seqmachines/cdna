import { getFirestore } from "firebase-admin/firestore";
import { getFirebaseApp } from "./firebase";

const COLLECTION = "protocols";

export interface Protocol {
  slug: string;
  title: string;
  source: string;
  content: string;
  createdAt: string;
  updatedAt: string;
}

function db() {
  return getFirestore(getFirebaseApp());
}

export async function listProtocols(): Promise<Protocol[]> {
  const snap = await db().collection(COLLECTION).orderBy("updatedAt", "desc").get();
  return snap.docs.map((doc) => doc.data() as Protocol);
}

export async function getProtocol(slug: string): Promise<Protocol | null> {
  const doc = await db().collection(COLLECTION).doc(slug).get();
  return doc.exists ? (doc.data() as Protocol) : null;
}

export async function saveProtocol(
  slug: string,
  title: string,
  source: string,
  content: string
): Promise<Protocol> {
  const now = new Date().toISOString();
  const existing = await getProtocol(slug);

  const protocol: Protocol = {
    slug,
    title,
    source,
    content,
    createdAt: existing?.createdAt || now,
    updatedAt: now,
  };

  await db().collection(COLLECTION).doc(slug).set(protocol);
  return protocol;
}
