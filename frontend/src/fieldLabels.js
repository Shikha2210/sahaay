// Small shared helpers for the role workspaces (labels and formatting only - no data).
export const FIELD_LABEL = {
  name: 'Name', age: 'Age', phone: 'Phone number', village: 'Village', condition: 'Condition', diagnosis: 'Diagnosis', medications: 'Medications',
  allergies: 'Allergies', treatment_plan: 'Treatment plan', blood_pressure: 'Blood pressure', blood_glucose: 'Blood glucose', dosage: 'Dosage', last_visit: 'Last visit',
  identity_status: 'Identity status',
};
export const label = f => FIELD_LABEL[f] || f;
export const show = v => (v === null || v === undefined || v === '' ? '—' : String(v));
export const fmtDate = iso => { if (!iso) return '—'; const d = new Date(iso); return isNaN(d) ? '—' : d.toLocaleString(); };
export const greeting = () => { const h = new Date().getHours(); return h < 12 ? 'GOOD MORNING' : h < 18 ? 'GOOD AFTERNOON' : 'GOOD EVENING'; };
export const CATEGORY_LABEL = { DOCUMENT_VERIFIABLE: 'Document', PATIENT_VERIFIABLE: 'Patient-confirmed', CRITICAL_CLINICAL: 'Clinical', REQUIRES_REVIEW: 'Needs review' };
