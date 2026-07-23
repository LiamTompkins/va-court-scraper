class HelplineContact:
    def __init__(self, case_id, contact):
        self.case_id = case_id
        self.first_name = contact['firstname'] if 'firstname' in contact else ""
        self.last_name = contact['lastname']
        self.role = contact['role']
        self.adverse = contact['adverse'] if 'adverse' in contact else None
        self.reviewed = contact['reviewed'] if 'reviewed' in contact else None

        if len(self.first_name) > 50:
            self.first_name = self.first_name[:50]
        
        if len(self.last_name) > 50:
            self.last_name = self.last_name[:50]

        if self.adverse is None:
            role = self.role.lower()
            if role == 'tenant':
                self.adverse = False
            elif role == 'landlord' or role == 'propertymanager':
                self.adverse = True
        
        self.display_name = (self.first_name + " " + self.last_name).strip()
    
    def is_pp_contact(self, pp_contact):
        return pp_contact['elh_case_number'] == self.case_id
