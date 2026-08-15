"""
AI + Quantum Chatbot Web Application
-------------------------------------
Built by Muhammad Hozafa Abbasi
Lightweight Flask backend. ALL heavy lifting happens in the cloud:
  1. AI Brain      -> Groq API (see call_llm()) — free tier gives 1,000+
                       requests/day, far more than Gemini's free 20/day.
  2. Quantum Engine -> IBM Quantum Cloud (qiskit-ibm-runtime), falls back to a
                       tiny local Aer simulation (a handful of qubits, <1s,
                       negligible RAM) only if no IBM token is configured.
  3. Response       -> raw quantum result summarized back into plain English
                       by the same LLM.

SECURITY NOTE: The LLM never returns executable code. It returns a small
JSON "spec" (problem type + parameters). The backend maps that spec to one
of a few pre-built, audited Qiskit circuit templates. We deliberately avoid
exec()/eval() on model output.
"""

import os
import json
import logging
import time
import functools
import secrets
from datetime import datetime

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, g
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("quantum-chatbot")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///" + os.path.join(app.instance_path, "quantummind.db")
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("APP_ENV", "production") == "production"
os.makedirs(app.instance_path, exist_ok=True)
db = SQLAlchemy(app)

if app.config["SECRET_KEY"] == "dev-key-change-me":
    log.warning(
        "SECRET_KEY is not set — using an insecure default. Set SECRET_KEY in "
        "your .env / host environment variables before deploying."
    )

CSRF_PROTECTED_ENDPOINTS = {
    "login_page", "register_page", "admin_delete_user", "admin_send_broadcast",
}

def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]

@app.context_processor
def inject_csrf_token():
    return {
        "csrf_token": get_csrf_token,
        "now_utc": datetime.utcnow,
    }

@app.before_request
def check_csrf():
    if request.method == "POST" and request.endpoint in CSRF_PROTECTED_ENDPOINTS:
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(sent, expected):
            return jsonify({"error": "Security check failed — please refresh the page and try again."}), 400

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
IBM_QUANTUM_TOKEN = os.environ.get("IBM_QUANTUM_TOKEN")
IBM_QUANTUM_INSTANCE = os.environ.get("IBM_QUANTUM_INSTANCE")

llm_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)

    sessions = db.relationship("ChatSession", backref="user", cascade="all, delete-orphan")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

class ChatSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(120), default="New chat")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    messages = db.relationship("ChatMessage", backref="chat_session", cascade="all, delete-orphan",
                                order_by="ChatMessage.id")

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("chat_session.id"), nullable=False, index=True)
    role = db.Column(db.String(10), nullable=False)
    content_html = db.Column(db.Text, nullable=False)
    tag = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Broadcast(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.Text, nullable=False)
    target_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class BroadcastRead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    broadcast_id = db.Column(db.Integer, db.ForeignKey("broadcast.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    __table_args__ = (db.UniqueConstraint("broadcast_id", "user_id"),)

with app.app_context():
    db.create_all()
    admin_username = os.environ.get("ADMIN_USERNAME", "Abbasi")
    admin_email = os.environ.get("ADMIN_EMAIL", "abbasihozafa18@gmail.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "hozafaabbasi125")
    
    existing_admin = User.query.filter(
        (User.username == admin_username) | (User.email == admin_email)
    ).first()
    
    if not existing_admin:
        admin = User(username=admin_username, email=admin_email, is_admin=True)
        admin.set_password(admin_password)
        db.session.add(admin)
        try:
            db.session.commit()
            log.info("✓ Seeded admin account '%s' (%s)", admin_username, admin_email)
        except Exception as e:
            log.error("Failed to seed admin: %s", e)
            db.session.rollback()
    else:
        log.info("✓ Admin account already exists (%s)", existing_admin.username)

def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Not logged in."}), 401
            return redirect(url_for("login_page"))
        g.user = db.session.get(User, session["user_id"])
        if g.user is None:
            session.clear()
            return redirect(url_for("login_page"))
        g.user.last_seen = datetime.utcnow()
        db.session.commit()
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user.is_admin:
            log.warning("Non-admin user %s tried to access admin panel", g.user.username)
            return render_template("admin_denied.html", username=g.user.username), 403
        return view(*args, **kwargs)
    return wrapped

ALLOWED_TYPES = [
    "grover_search",
    "qaoa_maxcut",
    "quantum_random",
    "vqe_h2",
    "qmc_portfolio",
    "shor_factoring",
    "vqe_material",
    "general_answer",
]

def call_llm(system_prompt: str, user_prompt: str, expect_json: bool = False) -> str:
    if not llm_client:
        raise RuntimeError("GROQ_API_KEY is not configured on the server.")

    resp = llm_client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.2,
        max_tokens=500,
        response_format={"type": "json_object"} if expect_json else None,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.choices[0].message.content

import re

def get_spec_from_prompt(user_message: str) -> dict:
    text = user_message.lower()

    bits_match = re.search(r"\b([01]{2,6})\b", user_message)
    if bits_match and any(w in text for w in ["find", "search", "marked", "locate"]):
        marked = bits_match.group(1)
        return {
            "type": "grover_search",
            "params": {"num_qubits": len(marked), "marked_state": marked},
            "reasoning": f"Detected a search request for bitstring '{marked}'.",
        }

    if any(w in text for w in ["random", "coin flip", "coin toss", "dice", "randomly pick"]):
        qubits_match = re.search(r"(\d+)\s*(?:-)?\s*(?:qubit|bit)", text)
        num_qubits = int(qubits_match.group(1)) if qubits_match else 4
        return {
            "type": "quantum_random",
            "params": {"num_qubits": num_qubits},
            "reasoning": "Detected a request for randomness.",
        }

    if any(w in text for w in [
        "molecule", "molecular", "drug", "chemistry", "chemical", "compound",
        "protein", "bonding energy", "ground state", "hydrogen bond",
    ]):
        return {
            "type": "vqe_h2",
            "params": {},
            "reasoning": "Detected a molecular/chemistry question — running real VQE on the H2 molecule as a genuine (if small-scale) demo.",
        }

    if any(w in text for w in [
        "portfolio", "investment", "stocks", "financial risk", "asset allocation",
        "diversif", "risk of loss",
    ]):
        return {
            "type": "qmc_portfolio",
            "params": {},
            "reasoning": "Detected a portfolio/financial-risk question — running a real quantum Monte Carlo sampling demo.",
        }

    if any(w in text for w in [
        "shor", "factor", "factoring", "rsa", "cryptograph", "encryption",
        "post-quantum", "quantum vulnerab", "crack the code", "crack encryption",
    ]):
        n_match = re.search(r"\b(15|21)\b", user_message)
        N = int(n_match.group(1)) if n_match else 15
        return {
            "type": "shor_factoring",
            "params": {"N": N},
            "reasoning": "Detected a factoring/cryptography question — running a real but tiny-scale (N<=21) Shor's algorithm demo.",
        }

    if any(w in text for w in [
        "battery", "batteries", "material", "materials", "lattice", "electrode",
        "renewable energy", "solar cell", "superconduct",
    ]):
        return {
            "type": "vqe_material",
            "params": {},
            "reasoning": "Detected a materials/battery question — running real VQE on a toy 2-site fermionic hopping model.",
        }

    if any(w in text for w in ["group", "partition", "split into", "balance", "cluster", "route"]):
        nodes_match = re.search(r"(\d+)\s*(?:nodes|items|people|groups)", text)
        num_nodes = int(nodes_match.group(1)) if nodes_match else 4
        return {
            "type": "qaoa_maxcut",
            "params": {"num_nodes": num_nodes, "edges": [[i, i + 1] for i in range(num_nodes - 1)]},
            "reasoning": "Detected a grouping/partitioning request.",
        }

    return {"type": "general_answer", "params": {}, "reasoning": "No quantum component detected."}

def run_on_backend(circuit, shots=512, force_local=False):
    from qiskit import transpile
    import time

    start_time = time.time()

    if IBM_QUANTUM_TOKEN and not force_local:
        try:
            from qiskit_ibm_runtime import QiskitRuntimeService
            from qiskit import qasm3

            service = QiskitRuntimeService(
                channel="ibm_quantum_platform", token=IBM_QUANTUM_TOKEN
            )
            backend = service.backend(
                "ibmq_qasm_simulator"
            ) if IBM_QUANTUM_INSTANCE else None
            if backend is None:
                backend = service.least_busy(simulator=False, operational=True)

            transpiled = transpile(circuit, backend, optimization_level=3)
            job = backend.run(transpiled, shots=shots)
            result = job.result()
            counts = dict(result.get_counts())

        except Exception as e:
            log.warning("IBM Quantum failed (%s), falling back to local simulation", e)
            from qiskit_aer import AerSimulator
            backend = AerSimulator()
            transpiled = transpile(circuit, backend, optimization_level=3)
            job = backend.run(transpiled, shots=shots)
            result = job.result()
            counts = dict(result.get_counts())
    else:
        from qiskit_aer import AerSimulator
        backend = AerSimulator()
        transpiled = transpile(circuit, backend, optimization_level=3)
        job = backend.run(transpiled, shots=shots)
        result = job.result()
        counts = dict(result.get_counts())

    execution_time = time.time() - start_time

    gates_before = sum(circuit.count_ops().values())
    gates_after = sum(transpiled.count_ops().values())

    top_bitstring = max(counts.items(), key=lambda x: x[1])[0]
    return {
        "counts": counts,
        "top_result": top_bitstring,
        "backend": backend.name if hasattr(backend, "name") else "simulator",
        "circuit_stats": {
            "num_qubits": circuit.num_qubits,
            "circuit_depth": circuit.depth(),
            "num_instructions": len(circuit),
        },
        "optimization_stats": {
            "gates_before": gates_before,
            "gates_after": gates_after,
        },
        "execution_time_seconds": round(execution_time, 3),
    }

def run_quantum_spec(spec: dict) -> dict | None:
    if spec["type"] == "general_answer":
        return None

    if spec["type"] == "quantum_random":
        from qiskit import QuantumCircuit
        n = spec["params"].get("num_qubits", 4)
        circuit = QuantumCircuit(n)
        for i in range(n):
            circuit.h(i)
        circuit.measure_all()
        result = run_on_backend(circuit)
        return {
            "type": "Quantum Randomness",
            "meta": spec["params"],
            **result,
        }

    if spec["type"] == "grover_search":
        from qiskit import QuantumCircuit, QuantumRegister
        from qiskit.circuit.library import GroverOperator
        marked_state = spec["params"]["marked_state"]
        n = len(marked_state)

        qr = QuantumRegister(n)
        circuit = QuantumCircuit(qr)
        for i in range(n):
            circuit.h(qr[i])

        grover = GroverOperator(oracle=_oracle_from_bitstring(marked_state))
        circuit.compose(grover, inplace=True)
        circuit.measure_all()
        result = run_on_backend(circuit)
        return {
            "type": "Grover Search",
            "meta": {"marked_state": marked_state},
            **result,
        }

    if spec["type"] == "qaoa_maxcut":
        from qiskit.circuit.library import QAOAAnsatz
        from qiskit.quantum_info import SparsePauliOp, Statevector
        import numpy as np
        from scipy.optimize import minimize

        num_nodes = spec["params"].get("num_nodes", 4)
        edges = spec["params"].get("edges", [[i, i + 1] for i in range(num_nodes - 1)])

        # Max-Cut cost Hamiltonian: one ZZ term per edge, Z placed at the
        # two actual node positions that edge connects.
        pauli_terms = []
        for (i, j) in edges:
            label = ["I"] * num_nodes
            label[i] = "Z"
            label[j] = "Z"
            pauli_terms.append(("".join(label), 1.0))
        cost_hamiltonian = SparsePauliOp.from_list(pauli_terms)

        ansatz = QAOAAnsatz(cost_hamiltonian, reps=1)

        # Genuine (small-scale) classical optimization of the QAOA angles —
        # same real-optimizer approach as the VQE path, not fixed/dummy
        # values. This is also what fixes the "parameter_binds not
        # specified" crash: the circuit sent to the backend below has its
        # parameters bound to these optimized numbers, not left free.
        def expectation(params):
            bound = ansatz.assign_parameters(params)
            sv = Statevector.from_instruction(bound)
            return float(np.real(sv.expectation_value(cost_hamiltonian)))

        opt = minimize(
            expectation, np.zeros(ansatz.num_parameters),
            method="COBYLA", options={"maxiter": 100},
        )

        circuit = ansatz.assign_parameters(opt.x)
        circuit.measure_all()
        result = run_on_backend(circuit, shots=512)
        result["type"] = "QAOA (Max-Cut)"
        result["meta"] = {
            "num_nodes": num_nodes,
            "edges": edges,
            "optimizer": "COBYLA (classical, local)",
            "qaoa_iterations": int(opt.nfev),
        }
        return result

    if spec["type"] == "vqe_h2":
        # Genuine VQE: real published 2-qubit H2 Hamiltonian (bond length
        # 0.735 A), real COBYLA optimizer over an exact local statevector
        # (fast — VQE needs dozens of evaluations, impractical to queue on
        # real hardware per chat message), then ONE final circuit at the
        # converged parameters run for real on run_on_backend() as proof.
        from qiskit import QuantumCircuit
        from qiskit.quantum_info import SparsePauliOp, Statevector
        import numpy as np
        from scipy.optimize import minimize

        hamiltonian = SparsePauliOp.from_list([
            ("II", -1.0523732), ("IZ", 0.39793742), ("ZI", -0.39793742),
            ("ZZ", -0.01128010), ("XX", 0.18093119),
        ])

        def ansatz(theta):
            qc = QuantumCircuit(2)
            qc.ry(theta[0], 0); qc.ry(theta[1], 1); qc.cx(0, 1)
            qc.ry(theta[2], 0); qc.ry(theta[3], 1)
            return qc

        def expectation(theta):
            sv = Statevector.from_instruction(ansatz(theta))
            return float(np.real(sv.expectation_value(hamiltonian)))

        opt = minimize(expectation, np.zeros(4), method="COBYLA", options={"maxiter": 150})
        circuit = ansatz(opt.x.tolist())
        circuit.measure_all()

        result = run_on_backend(circuit, shots=512, force_local=True)
        result["type"] = "VQE (H2 molecule)"
        result["meta"] = {
            "molecule": "H2, toy demo scale (0.735 A bond length)",
            "optimizer": "COBYLA (classical, local)",
            "vqe_iterations": int(opt.nfev),
        }
        result["ground_state_energy_hartree"] = round(float(opt.fun), 6)
        return result

    if spec["type"] == "qmc_portfolio":
        # Genuine sampling-based quantum Monte Carlo: each asset's risk
        # (volatility - return) is encoded as a rotation angle, entangled
        # for simplified correlation, then real measurement shots sample
        # the joint loss distribution — real risk stats, not invented.
        from qiskit import QuantumCircuit
        import numpy as np

        assets = spec["params"].get("assets") or [
            {"name": "Asset A", "expected_return": 0.07, "volatility": 0.15},
            {"name": "Asset B", "expected_return": 0.04, "volatility": 0.08},
            {"name": "Asset C", "expected_return": 0.10, "volatility": 0.25},
        ]
        n = max(2, min(len(assets), 6))
        assets = assets[:n]

        circuit = QuantumCircuit(n, n)
        for i, a in enumerate(assets):
            risk_score = min(max(a["volatility"] - a["expected_return"], 0.01), 0.99)
            circuit.ry(np.pi * risk_score, i)
        for i in range(n - 1):
            circuit.cx(i, i + 1)
        circuit.measure(range(n), range(n))

        result = run_on_backend(circuit, shots=512)
        total = sum(result["counts"].values())
        all_down = sum(v for k, v in result["counts"].items() if k.count("1") == n) / total
        any_down = sum(v for k, v in result["counts"].items() if "1" in k) / total
        result["type"] = "Quantum Monte Carlo (Portfolio)"
        result["meta"] = {
            "assets": assets,
            "risk_stats": {
                "probability_all_assets_down": round(all_down, 4),
                "probability_at_least_one_down": round(any_down, 4),
                "shots": total,
            },
        }
        return result

    if spec["type"] == "shor_factoring":
        # Genuine Shor's-algorithm order-finding: quantum phase estimation
        # over controlled modular multiplication, built as exact permutation
        # matrices (guaranteed unitary since x -> a*x mod N is a bijection
        # when gcd(a, N) = 1), then real classical continued-fractions
        # post-processing. HARD SCOPE LIMIT: N capped to 15/21 — this is an
        # educational algorithm demo, NOT a real cryptography security audit
        # (real RSA keys need thousands of error-corrected qubits to factor).
        from qiskit import QuantumCircuit
        from qiskit.circuit.library import QFT, UnitaryGate
        from fractions import Fraction
        from math import gcd
        import numpy as np

        N = spec["params"].get("N", 15)
        if N not in (15, 21):
            N = 15
        a = {15: 7, 21: 2}[N]
        n_work = int(np.ceil(np.log2(N)))
        n_count = 2 * n_work
        dim = 2 ** n_work

        def mult_mod_matrix(mult):
            U = np.zeros((dim, dim))
            for y in range(dim):
                U[(mult * y) % N, y] = 1 if y < N else 0
                if y >= N:
                    U[y, y] = 1
            return U

        circuit = QuantumCircuit(n_count + n_work, n_count)
        circuit.h(range(n_count))
        circuit.x(n_count)
        for j in range(n_count):
            power = pow(a, 2 ** j, N)
            gate = UnitaryGate(mult_mod_matrix(power), label=f"a^{2**j} mod {N}").control(1)
            circuit.append(gate, [j] + list(range(n_count, n_count + n_work)))
        circuit.append(QFT(n_count, inverse=True), range(n_count))
        circuit.measure(range(n_count), range(n_count))

        result = run_on_backend(circuit, shots=1024, force_local=True)
        factors_found = set()
        for bitstring in result["counts"]:
            phase = int(bitstring, 2) / (2 ** n_count)
            r = Fraction(phase).limit_denominator(N).denominator
            if r and r % 2 == 0:
                for guess in (gcd(pow(a, r // 2) - 1, N), gcd(pow(a, r // 2) + 1, N)):
                    if guess not in (1, N) and N % guess == 0:
                        factors_found.add(guess)

        result["type"] = f"Shor's Algorithm (N={N}, educational demo — not a security audit)"
        result["meta"] = {
            "N": N, "a": a,
            "factors_found": sorted(factors_found) or "no nontrivial factor recovered this run (Shor's is probabilistic — retry)",
            "scope_note": "N<=21 hard limit by design — not an assessment of real cryptographic security.",
        }
        return result

    if spec["type"] == "vqe_material":
        # Genuine VQE on a minimal 2-site fermionic hopping (tight-binding/
        # Hubbard-like) model — Jordan-Wigner mapped, real classical COBYLA
        # optimization. NOT a specific real battery compound — a toy-scale
        # demo of the actual technique.
        from qiskit import QuantumCircuit
        from qiskit.quantum_info import SparsePauliOp, Statevector
        import numpy as np
        from scipy.optimize import minimize

        t = float(spec["params"].get("hopping_t", 1.0))
        U_onsite = float(spec["params"].get("onsite_U", 2.0))
        hamiltonian = SparsePauliOp.from_list([
            ("II", U_onsite / 4), ("ZI", -U_onsite / 4), ("IZ", -U_onsite / 4),
            ("ZZ", U_onsite / 4), ("XX", -t / 2), ("YY", -t / 2),
        ])

        def ansatz(theta):
            qc = QuantumCircuit(2)
            qc.ry(theta[0], 0); qc.ry(theta[1], 1); qc.cx(0, 1)
            qc.ry(theta[2], 0); qc.ry(theta[3], 1)
            return qc

        def expectation(theta):
            sv = Statevector.from_instruction(ansatz(theta))
            return float(np.real(sv.expectation_value(hamiltonian)))

        opt = minimize(expectation, np.zeros(4), method="COBYLA", options={"maxiter": 150})
        circuit = ansatz(opt.x.tolist())
        circuit.measure_all()

        result = run_on_backend(circuit, shots=512, force_local=True)
        result["type"] = "VQE (Materials / 2-site fermionic hopping model)"
        result["meta"] = {
            "model": "2-site tight-binding/Hubbard-like toy model — not a specific real battery compound",
            "hopping_t": t, "onsite_U": U_onsite,
            "optimizer": "COBYLA (classical, local)",
            "vqe_iterations": int(opt.nfev),
        }
        result["ground_state_energy_model_units"] = round(float(opt.fun), 6)
        return result

    return None

def _oracle_from_bitstring(bitstring):
    from qiskit import QuantumCircuit
    from qiskit.circuit.library import ZGate
    n = len(bitstring)
    circuit = QuantumCircuit(n)
    for i, bit in enumerate(bitstring):
        if bit == "0":
            circuit.x(i)
    if n > 1:
        circuit.barrier()
        controls = list(range(n - 1))
        target = n - 1
        # QuantumCircuit has no built-in mcz — build a multi-controlled Z
        # by taking a plain ZGate and adding (n-1) control qubits to it.
        circuit.append(ZGate().control(n - 1), controls + [target])
        circuit.barrier()
    for i, bit in enumerate(bitstring):
        if bit == "0":
            circuit.x(i)
    return circuit.to_gate(label="oracle")

ASSISTANT_IDENTITY = """You are QuantumMind, a helpful AI + Quantum assistant built by Muhammad Hozafa Abbasi.
You are concise, upbeat, and precise. Speak in plain English, never jargon.

Do NOT:
- Oversell the quantum compute. It's genuinely interesting but limited in scale.
- Claim quantum advantage. State only real measured results.
- Use raw code, JSON, or tables in your response.

Do explain:
- What was asked.
- Which quantum algorithm(s) ran, and why it was a good fit.
- The real measured result (bitstring counts, energy, etc).
- Plain English interpretation."""

SUMMARY_SYSTEM_PROMPT = f"""{ASSISTANT_IDENTITY}

You have run a Quantum circuit and have real measured results. You are summarizing them.

Format: 1 short paragraph (3-4 sentences max). Lead with what the question was, then the technique, then the real result in plain terms.
Do not show raw code. Do not use JSON in your answer."""

def summarize_result(user_message: str, spec: dict, quantum_result: dict | None) -> str:
    if quantum_result is None:
        return call_llm(
            f"{ASSISTANT_IDENTITY}\n\nYou are concise and helpful. Answer normally in plain English.",
            user_message,
        )

    context = (
        f"Original question: {user_message}\n"
        f"Quantum technique used: {quantum_result['type']}\n"
        f"Circuit parameters: {json.dumps(quantum_result['meta'])}\n"
        f"Backend: {quantum_result['backend']}\n"
        f"Circuit stats (REAL, measured): {json.dumps(quantum_result['circuit_stats'])}\n"
        f"Optimization stats (REAL, before/after transpile): {json.dumps(quantum_result['optimization_stats'])}\n"
        f"Wall-clock execution_time_seconds (REAL, measured): {quantum_result['execution_time_seconds']}\n"
        f"Measurement counts: {json.dumps(quantum_result['counts'])}\n"
        f"Most frequent outcome (bitstring): {quantum_result['top_result']}\n"
    )
    if "ground_state_energy_hartree" in quantum_result:
        context += f"Ground state energy in Hartree units: {quantum_result['ground_state_energy_hartree']}\n"
    if "ground_state_energy_model_units" in quantum_result:
        context += f"Ground state energy in model units: {quantum_result['ground_state_energy_model_units']}\n"
    return call_llm(SUMMARY_SYSTEM_PROMPT, context)

@app.route("/register", methods=["GET", "POST"])
def register_page():
    if request.method == "GET":
        return render_template("register.html")

    username = (request.form.get("username") or "").strip()
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    
    if not username or not password:
        return render_template("register.html", error="Name and password are required.")
    if len(password) < 4:
        return render_template("register.html", error="Password must be at least 4 characters.")
    if User.query.filter_by(username=username).first():
        return render_template("register.html", error="That name is already taken.")

    user = User(username=username, email=email if email else None)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    session["user_id"] = user.id
    return redirect(url_for("index"))

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        return render_template("login.html")

    login_input = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    
    user = User.query.filter_by(username=login_input).first()
    if not user and "@" in login_input:
        user = User.query.filter_by(email=login_input).first()
    
    if not user or not user.check_password(password):
        return render_template("login.html", error="Wrong name/email or password.")

    session["user_id"] = user.id
    user.last_seen = datetime.utcnow()
    db.session.commit()
    
    if user.is_admin:
        log.info("✓ Admin '%s' logged in", user.username)
        return redirect(url_for("admin_page"))
    else:
        log.info("✓ User '%s' logged in", user.username)
        return redirect(url_for("index"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

@app.route("/")
@login_required
def index():
    return render_template("index.html", username=g.user.username, is_admin=g.user.is_admin)

@app.route("/api/sessions", methods=["GET", "POST"])
@login_required
def api_sessions():
    if request.method == "POST":
        cs = ChatSession(user_id=g.user.id, title="New chat")
        db.session.add(cs)
        db.session.commit()
        return jsonify({"id": cs.id, "title": cs.title})

    rows = (ChatSession.query.filter_by(user_id=g.user.id)
            .order_by(ChatSession.updated_at.desc()).all())
    return jsonify([{"id": r.id, "title": r.title} for r in rows])

@app.route("/api/sessions/<int:session_id>", methods=["GET", "PATCH", "DELETE"])
@login_required
def api_session_detail(session_id):
    cs = ChatSession.query.filter_by(id=session_id, user_id=g.user.id).first()
    if not cs:
        return jsonify({"error": "Chat not found."}), 404

    if request.method == "DELETE":
        db.session.delete(cs)
        db.session.commit()
        return jsonify({"ok": True})

    if request.method == "PATCH":
        data = request.get_json(silent=True) or {}
        new_title = (data.get("title") or "").strip()
        if not new_title:
            return jsonify({"error": "Title is required."}), 400
        cs.title = new_title[:120]
        cs.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"id": cs.id, "title": cs.title})

    msgs = [{"role": m.role, "html": m.content_html, "tag": m.tag} for m in cs.messages]
    return jsonify({"id": cs.id, "title": cs.title, "messages": msgs})

@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    session_id = data.get("session_id")
    if not user_message:
        return jsonify({"error": "Message is required."}), 400

    cs = ChatSession.query.filter_by(id=session_id, user_id=g.user.id).first() if session_id else None
    if not cs:
        cs = ChatSession(user_id=g.user.id, title="New chat")
        db.session.add(cs)
        db.session.commit()

    # --- Step 1: figure out what kind of problem this is. This is just
    # regex/keyword matching (no network call), but wrapped anyway so a
    # weird message can never take the whole reply down.
    try:
        spec = get_spec_from_prompt(user_message)
    except Exception:
        log.exception("Intent detection failed for message: %r", user_message)
        spec = {"type": "general_answer", "params": {}, "reasoning": "Fell back after a classification error."}

    # --- Step 2: try to run the matching quantum circuit. If IBM Quantum,
    # the local simulator, or the circuit itself has a problem, we do NOT
    # fail the whole request — we just degrade to a plain-language answer
    # with no quantum step, and log the real cause for the admin to see.
    quantum_result = None
    if spec["type"] != "general_answer":
        try:
            quantum_result = run_quantum_spec(spec)
        except Exception:
            log.exception("Quantum pipeline failed for spec=%s", spec)
            quantum_result = None

    # --- Step 3: ask the AI to phrase the answer. If Groq is rate-limited,
    # unreachable, or misconfigured, give a clear, friendly message instead
    # of a raw exception string.
    try:
        reply = summarize_result(user_message, spec, quantum_result)
    except Exception as exc:
        log.exception("LLM summarize failed")
        msg = str(exc).lower()
        if "rate_limit" in msg or "429" in msg:
            reply = ("I'm getting a lot of requests right now (Groq's free-tier "
                      "limit) — please wait a few seconds and try again.")
        elif "groq_api_key" in msg or "not configured" in msg:
            reply = ("The AI service isn't configured on the server yet — "
                      "please let the site admin know.")
        else:
            reply = ("Sorry, I couldn't reach the AI service just now. "
                      "Please try again in a moment.")

    tag = (f"{quantum_result['type']} · {quantum_result['backend']} · "
           f"{quantum_result['circuit_stats']['num_qubits']}q/"
           f"{quantum_result['optimization_stats']['gates_after']} gates "
           f"(opt from {quantum_result['optimization_stats']['gates_before']}) · "
           f"{quantum_result['execution_time_seconds']}s") if quantum_result else \
          "general reasoning (no quantum step needed)"

    try:
        db.session.add(ChatMessage(session_id=cs.id, role="user", content_html=user_message))
        db.session.add(ChatMessage(session_id=cs.id, role="bot", content_html=reply, tag=tag))
        if ChatMessage.query.filter_by(session_id=cs.id, role="user").count() == 1:
            cs.title = (user_message[:42] + "…") if len(user_message) > 42 else user_message
        cs.updated_at = datetime.utcnow()
        db.session.commit()
    except Exception:
        log.exception("Failed to save chat message to the database")
        db.session.rollback()

    return jsonify({
        "reply": reply,
        "spec": spec,
        "quantum": quantum_result,
        "session_id": cs.id,
        "tag": tag,
    })

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "llm_configured": bool(GROQ_API_KEY),
        "ibm_quantum_configured": bool(IBM_QUANTUM_TOKEN),
    })

@app.route("/api/broadcasts/pending")
@login_required
def api_broadcasts_pending():
    seen_ids = {r.broadcast_id for r in BroadcastRead.query.filter_by(user_id=g.user.id).all()}
    rows = Broadcast.query.filter(
        db.or_(Broadcast.target_user_id == g.user.id, Broadcast.target_user_id.is_(None))
    ).order_by(Broadcast.created_at.asc()).all()
    pending = [{"id": b.id, "message": b.message} for b in rows if b.id not in seen_ids]
    return jsonify(pending)

@app.route("/api/broadcasts/<int:broadcast_id>/ack", methods=["POST"])
@login_required
def api_broadcast_ack(broadcast_id):
    if not BroadcastRead.query.filter_by(broadcast_id=broadcast_id, user_id=g.user.id).first():
        db.session.add(BroadcastRead(broadcast_id=broadcast_id, user_id=g.user.id))
        db.session.commit()
    return jsonify({"ok": True})

@app.route("/admin")
@admin_required
def admin_page():
    users = User.query.order_by(User.created_at.desc()).all()
    rows = []
    for u in users:
        session_count = ChatSession.query.filter_by(user_id=u.id).count()
        message_count = (db.session.query(ChatMessage)
                          .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                          .filter(ChatSession.user_id == u.id).count())
        rows.append({
            "id": u.id, "username": u.username, "email": u.email or "—",
            "is_admin": u.is_admin, "created_at": u.created_at, "last_seen": u.last_seen,
            "session_count": session_count, "message_count": message_count,
        })
    broadcasts = Broadcast.query.order_by(Broadcast.created_at.desc()).limit(20).all()
    return render_template("admin.html", users=rows, broadcasts=broadcasts,
                            all_users=[u for u in users if not u.is_admin],
                            username=g.user.username, is_admin=g.user.is_admin)

@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    u = db.session.get(User, user_id)
    if u and not u.is_admin:
        log.info("Admin '%s' deleted user '%s'", g.user.username, u.username)
        db.session.delete(u)
        db.session.commit()
    return redirect(url_for("admin_page"))

@app.route("/admin/broadcast", methods=["POST"])
@admin_required
def admin_send_broadcast():
    message = (request.form.get("message") or "").strip()
    target = request.form.get("target", "all")
    if message:
        target_user_id = None if target == "all" else int(target)
        db.session.add(Broadcast(message=message, target_user_id=target_user_id,
                                  created_by=g.user.username))
        db.session.commit()
        log.info("Admin '%s' sent broadcast", g.user.username)
    return redirect(url_for("admin_page"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)