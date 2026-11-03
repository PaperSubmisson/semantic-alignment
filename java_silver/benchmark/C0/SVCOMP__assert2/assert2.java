import java.util.Random;

class assert2 {
  public static void main() {
    int i = new Random().nextInt();

    if (i >= 1000) assert i >= 1000 : "i is greater 1000";
  }
}
