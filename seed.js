import { initializeApp } from "firebase/app";
import { getFirestore, doc, setDoc } from "firebase/firestore";

// 🔥 PASTE YOUR CONFIG HERE (NOT ENV FOR SCRIPT)
const firebaseConfig = {
  apiKey: "AIzaSyCGu1nmbD7arFk6E7j4TGZRSb5mau1Uv-A",
  authDomain: "dh-judge-platform.firebaseapp.com",
  projectId: "dh-judge-platform",
};

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);

// 🔥 SAMPLE PROJECTS
const projects = [
  {
    id: "proj1",
    name: "EcoML Predictor",
    tracks: ["AI/ML"],
    tableNumber: 1,
    members: ["Alice Chen", "Brian Kim"],
  },
  {
    id: "proj2",
    name: "Wildfire Vision",
    tracks: ["AI/ML", "Data Analytics"],
    tableNumber: 2,
    members: ["David Park", "Emily Zhang", "Chris Wong"],
  },
  {
    id: "proj3",
    name: "Carbon Tracker",
    tracks: ["Data Analytics"],
    tableNumber: 3,
    members: ["Michael Lee", "Sara Ahmed"],
  },
  {
    id: "proj4",
    name: "Smart Irrigation",
    tracks: ["Hardware & IoT", "AI/ML"],
    tableNumber: 4,
    members: ["Jason Patel", "Nina Gupta", "Leo Martinez"],
  },
  {
    id: "proj5",
    name: "Ocean Health AI",
    tracks: ["AI/ML"],
    tableNumber: 5,
    members: ["Ethan Nguyen"],
  },
];

// 🔥 SAMPLE JUDGES
const judges = [
  {
    id: "judge1",
    name: "Ansh",
    email: "abhatnagar@ucsd.edu",
    track: "AI/ML",
    assignedProjects: ["proj1", "proj2", "proj3", "proj4", "proj5"],
  },
  {
    id: "judge2",
    name: "Sarah",
    email: "sarah@gmail.com",
    track: "AI/ML",
    assignedProjects: ["proj1", "proj2"],
  },
];

// 🔥 UPLOAD FUNCTION
const seed = async () => {
  console.log("Uploading projects...");

  for (let p of projects) {
    await setDoc(doc(db, "projects", p.id), {
      name: p.name,
      tracks: p.tracks,
      tableNumber: p.tableNumber,
      members: p.members,
    });
  }

  console.log("Uploading judges...");

  for (let j of judges) {
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