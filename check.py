import os
from dotenv import load_dotenv
from qiskit_ibm_runtime import QiskitRuntimeService

load_dotenv()

TOKEN = os.environ.get("IBM_QUANTUM_TOKEN")
if not TOKEN:
    raise SystemExit("Set IBM_QUANTUM_TOKEN in your .env file first.")

service = QiskitRuntimeService(channel="ibm_quantum_platform", token=TOKEN)

print("Instances:")
print(service.instances())

print("\nBackends:")
print(service.backends())
