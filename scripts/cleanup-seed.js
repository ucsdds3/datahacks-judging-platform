import { initializeApp } from "firebase/app";
import { getFirestore, doc, deleteDoc } from "firebase/firestore";

const firebaseConfig = {
  apiKey: "AIzaSyCGu1nmbD7arFk6E7j4TGZRSb5mau1Uv-A",
  authDomain: "dh-judge-platform.firebaseapp.com",
  projectId: "dh-judge-platform",
};

const db = getFirestore(initializeApp(firebaseConfig));

// Stale IDs from seed.js that assign.js never overwrote
const staleProjects = ["proj1", "proj2", "proj3", "proj4", "proj5"];
const staleJudges   = ["judge2"]; // seed.js test judge

for (const id of staleProjects) {
  await deleteDoc(doc(db, "projects", id));
  console.log(`deleted project: ${id}`);
}
for (const id of staleJudges) {
  await deleteDoc(doc(db, "judges", id));
  console.log(`deleted judge: ${id}`);
}

console.log("Done ✅");
process.exit(0);
