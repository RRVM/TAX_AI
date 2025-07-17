def calculate_old_regime(data):
    # Extract values, default to 0 if missing
    gross_salary = float(data.get('gross_salary') or 0)
    basic_salary = float(data.get('basic_salary') or 0)
    hra_received = float(data.get('hra_received') or 0)
    rent_paid = float(data.get('rent_paid') or 0)
    deduction_80c = float(data.get('deduction_80c') or 0)
    deduction_80d = float(data.get('deduction_80d') or 0)
    standard_deduction = float(data.get('standard_deduction') or 50000)
    professional_tax = float(data.get('professional_tax') or 0)
    tds = float(data.get('tds') or 0)

    # HRA exemption (simplified):
    # Least of (i) HRA received, (ii) 50% of basic (metro) or 40% (non-metro), (iii) rent paid - 10% of basic
    # We'll assume non-metro (40%) for simplicity
    hra_exempt = min(
        hra_received,
        0.4 * basic_salary,
        max(0, rent_paid - 0.1 * basic_salary)
    )

    # Total deductions
    total_deductions = (
        standard_deduction + hra_exempt + professional_tax + deduction_80c + deduction_80d
    )
    taxable_income = max(0, gross_salary - total_deductions)

    # Old regime slabs
    tax = 0
    slabs = [
        (250000, 0.0),
        (500000, 0.05),
        (1000000, 0.2),
        (float('inf'), 0.3)
    ]
    prev_limit = 0
    for limit, rate in slabs:
        if taxable_income > limit:
            tax += (limit - prev_limit) * rate
            prev_limit = limit
        else:
            tax += (taxable_income - prev_limit) * rate
            break
    # 4% cess
    tax *= 1.04
    return round(tax, 2)

def calculate_new_regime(data):
    gross_salary = float(data.get('gross_salary') or 0)
    standard_deduction = float(data.get('standard_deduction') or 50000)
    taxable_income = max(0, gross_salary - standard_deduction)
    # New regime slabs
    tax = 0
    slabs = [
        (300000, 0.0),
        (600000, 0.05),
        (900000, 0.1),
        (1200000, 0.15),
        (1500000, 0.2),
        (float('inf'), 0.3)
    ]
    prev_limit = 0
    for limit, rate in slabs:
        if taxable_income > limit:
            tax += (limit - prev_limit) * rate
            prev_limit = limit
        else:
            tax += (taxable_income - prev_limit) * rate
            break
    # 4% cess
    tax *= 1.04
    return round(tax, 2)

def calculate_tax_comparison(data):
    tax_old = calculate_old_regime(data)
    tax_new = calculate_new_regime(data)
    best_regime = 'old' if tax_old < tax_new else 'new'
    return {
        'tax_old_regime': tax_old,
        'tax_new_regime': tax_new,
        'best_regime': best_regime
    } 