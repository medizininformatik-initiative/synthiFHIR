import de.uni_leipzig.imise.validate.FHIRValidator;
import de.uni_leipzig.imise.validate.FHIRValidator.ValidationResultType;

/** Exercise the real validator and bundled profiles using only the distributed JAR. */
public class CheckPackagedValidation {
    public static void main(String[] args) throws Exception {
        try {
            Class.forName("ognl.Ognl");
            throw new AssertionError("The distributed JAR must not contain OGNL");
        } catch (ClassNotFoundException expected) {
            // Optional narrative expression evaluation is not part of the converter.
        }
        FHIRValidator validator = new FHIRValidator();
        String patient = "{\"resourceType\":\"Patient\",\"id\":\"validation-probe\",\"gender\":\"%s\"}";
        ValidationResultType valid = validator.validate(String.format(patient, "female"), true);
        if (valid != ValidationResultType.VALID && valid != ValidationResultType.WARNING) {
            throw new AssertionError("Valid patient was not checked successfully: " + valid);
        }
        ValidationResultType invalid = validator.validate(String.format(patient, "invalid-gender"), true);
        if (invalid != ValidationResultType.ERROR) {
            throw new AssertionError("Invalid gender was not reported: " + invalid);
        }
        System.out.println("Packaged FHIR validation passed without OGNL");
    }
}
