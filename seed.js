import { initializeApp } from "firebase/app";
import { getFirestore, doc, setDoc } from "firebase/firestore";
import { readFileSync } from "fs";

const firebaseConfig = {
  apiKey: "AIzaSyCGu1nmbD7arFk6E7j4TGZRSb5mau1Uv-A",
  authDomain: "dh-judge-platform.firebaseapp.com",
  projectId: "dh-judge-platform",
};

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);

const { projects, judges } = JSON.parse(readFileSync("assignments.local.json", "utf8"));

const seed = async () => {
  console.log(`Uploading ${projects.length} projects...`);
  for (const p of projects) {
    await setDoc(doc(db, "projects", p.id), {
      name: p.name,
      tracks: p.tracks,
      tableNumber: p.tableNumber,
    });
  }

  console.log(`Uploading ${judges.length} judges...`);
  for (const j of judges) {
    await setDoc(doc(db, "judges", j.id), {
      name: j.name,
      email: j.email,
      track: j.track,
      assignedProjects: j.assignedProjects,
    });
  }

  console.log("DONE ✅");
};

seed();
