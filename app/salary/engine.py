from app.storage.models import Employee


class SalaryEngine:
    def calculate(self, employee: Employee) -> int:
        return employee.salary_amount

