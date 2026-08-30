
# Brief overview: The payday lending platform (us) gets payroll data from 10 different providers and ingests them using our platform that standises all of the data from the platforms -> allows requests of platform users to get a loan -> checks whether they qualify for the loan 

# numbered workflow 

1. Each organisation's payroll system is read by a dedicated adapter, which converts the data into a common employee and salary schema and stores it in the platform database.
2. An employee applies for a loan through the app.
3. The credit scoring service (for example a logistic regression model) uses the normalised payroll history to produce a score, and the decision engine turns that into an approved amount and interest rate.
4. The disbursement service draws on the ZAR float at the partner bank via Open Banking and sends the employee a cash voucher by SMS, WhatsApp or email, which they redeem at an ATM.
5. On payday the reminder service notifies the employee. They repay at a supermarket financial services desk using their account number, and the reconciliation service matches the payment to the loan.

# diagram 

```mermaid
flowchart TD
    A[10 payroll providers] --> B[Dedicated adapters<br/>per organisation]
    B --> C[Standardised employee & salary schema]
    C --> D[(Platform database)]

    E[Employee] -->|Applies for loan via app| F[Loan application]
    F --> G{Credit scoring service<br/>e.g. logistic regression}
    D --> G
    G --> H[Decision engine]
    H --> I{Approved?}
    I -->|Yes: amount & rate set| J[Disbursement service]
    I -->|No| Z[Application declined]

    J -->|Draws ZAR float via Open Banking| K[Partner bank]
    J -->|Sends cash voucher| L[SMS / WhatsApp / Email]
    L --> M[Employee redeems voucher at ATM]

    N[Reminder service] -->|Notifies on payday| E
    E -->|Repays with account number| O[Supermarket financial<br/>services desk]
    O --> P[Reconciliation service]
    P -->|Matches payment to loan| D
```
